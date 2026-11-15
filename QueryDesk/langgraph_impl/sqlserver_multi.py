"""Explicit same-instance SQL Server object mappings; shared governance uses logical IDs."""
import re
from copy import deepcopy

from .storage import QueryError
from .warehouse import WarehouseDatabase


def name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',value):
        raise ValueError('Mapped SQL Server identifiers must be simple names of at most 128 characters')
    return value


def validate_mappings(allowed,mappings,relationships=None):
    if not isinstance(mappings,dict) or not 1<=len(mappings)<=500 or set(mappings)!=set(allowed):
        raise ValueError('table_mappings must exactly match allowed_tables')
    physical=set()
    for alias,target in mappings.items():
        if not isinstance(alias,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}',alias):
            raise ValueError('Mapped table IDs must be lowercase simple names of at most 63 characters')
        if not isinstance(target,dict) or set(target)!={'database','schema','table'}:
            raise ValueError('Each table mapping requires database, schema and table')
        identity=tuple(name(target[key]).casefold() for key in ('database','schema','table'))
        if identity in physical:raise ValueError('Physical table mappings must be unique, ignoring case')
        physical.add(identity)
    relationships=[] if relationships is None else relationships
    if not isinstance(relationships,list) or len(relationships)>500:raise ValueError('Invalid mapped relationships')
    for relation in relationships:
        if not isinstance(relation,dict) or set(relation)!={'from_table','to_table','columns'}:
            raise ValueError('Mapped relationships require from_table, to_table and columns')
        if relation['from_table'] not in mappings or relation['to_table'] not in mappings:
            raise ValueError('Relationships must reference registered logical table IDs')
        pairs=relation['columns']
        if not isinstance(pairs,dict) or not 1<=len(pairs)<=8 or len(set(pairs.values()))!=len(pairs):raise ValueError('Invalid relationship column pairs')
        for local,remote in pairs.items():name(local);name(remote)
    return deepcopy(mappings),deepcopy(relationships)


class SQLServerMultiDatabase(WarehouseDatabase):
    def __init__(self,address,allowed_tables,mappings,relationships=None,schema='dbo',**limits):
        self.table_mappings,self.relationships=validate_mappings(allowed_tables,mappings,relationships)
        super().__init__(address,'mssql',allowed_tables,schema,**limits)

    def discover_tables(self,connection):
        from sqlalchemy import inspect
        inspector=inspect(connection);names={};available=set()
        for alias,target in self.table_mappings.items():
            namespace=target['database']+'.'+target['schema']
            if namespace not in names:names[namespace]=set(inspector.get_table_names(schema=namespace))
            if target['table'] in names[namespace]:available.add(alias)
        return available

    def catalog(self):
        return {'dialect':'mssql','default_schema':None,'tables':sorted(self.allowed),'same_instance_cross_database':True}

    def table_options(self,tables):
        if not set(tables)<=self.allowed:raise QueryError('Choose permitted mapped table IDs')
        return [{'id':table,**self.table_mappings[table]} for table in sorted(set(tables))]

    def schema(self,tables):
        from sqlalchemy import inspect
        if not tables or not set(tables)<=self.allowed:raise QueryError('Choose permitted mapped table IDs')
        metadata=[]
        with self.connection() as connection:
            inspector=inspect(connection);cache={};regular={}
            def columns(alias):
                if alias not in cache:
                    target=self.table_mappings[alias];namespace=target['database']+'.'+target['schema']
                    if namespace not in regular:regular[namespace]=set(inspector.get_table_names(schema=namespace))
                    if target['table'] not in regular[namespace]:raise QueryError('Mapped object is missing or is not a regular table')
                    primary=set(inspector.get_pk_constraint(target['table'],schema=namespace).get('constrained_columns',[]))
                    cache[alias]=[{'name':c['name'],'type':str(c['type']),'primary_key':c['name'] in primary,'nullable':bool(c['nullable'])} for c in inspector.get_columns(target['table'],schema=namespace)]
                return cache[alias]
            for alias in sorted(set(tables)):
                target=self.table_mappings[alias];items=columns(alias);links=[]
                # SQL Server has no native cross-database FK constraints. These
                # relationships are explicit operator metadata, never invented.
                for relation in self.relationships:
                    if relation['from_table']!=alias or relation['to_table'] not in self.allowed:continue
                    remote=relation['to_table'];left={c['name'] for c in items};right={c['name'] for c in columns(remote)}
                    if not set(relation['columns'])<=left or not set(relation['columns'].values())<=right:raise QueryError('Configured relationship columns no longer match source metadata')
                    links.extend({'column':local,'references_table':remote,'references_column':column,'origin':'operator_config'} for local,column in relation['columns'].items())
                if hasattr(self,'security_projection'):
                    items=[c for c in items if c['name'] in self.security_projection.get(alias,[])]
                    links=[link for link in links if link['column'] in self.security_projection.get(alias,[]) and link['references_column'] in self.security_projection.get(link['references_table'],[])]
                metadata.append({'table':alias,'physical_table':deepcopy(target),'columns':items,'foreign_keys':links})
        return {'dialect':'mssql','default_schema':None,'same_instance_cross_database':True,'tables':metadata}

    def checked_sql(self,sql,parameters,tables):
        import sqlglot
        from sqlglot import exp
        from sqlglot.optimizer.scope import Scope,traverse_scope
        from .storage import Database
        Database.inputs(self,sql,parameters,tables)
        try:statements=sqlglot.parse(sql,read='tsql')
        except sqlglot.errors.ParseError:raise QueryError('SQL does not parse in the selected dialect') from None
        if len(statements)!=1 or not isinstance(statements[0],(exp.Select,exp.Union,exp.Intersect,exp.Except)):
            raise QueryError('Only one read-only SELECT is permitted')
        tree=statements[0]
        self.check_read_tree(tree)
        # Resolve original source scopes first, before replacing physical names.
        # Qualified logical references avoid a CTE shadowing a mapped base table.
        try:scopes=list(traverse_scope(tree))
        except sqlglot.errors.OptimizeError:raise QueryError('Use distinct explicit SQL aliases for mapped tables') from None
        for scope in scopes:
            resolved={}
            for alias,source in scope.sources.items():
                if isinstance(source,Scope):continue
                if not isinstance(source,exp.Table) or not all(isinstance(part,exp.Identifier) for part in source.parts):
                    raise QueryError('External sources and table functions are not permitted')
                if any(value for key,value in source.args.items() if key not in {'this','db','catalog','alias'}) or (source.args.get('alias') and source.args['alias'].args.get('columns')):
                    raise QueryError('Mapped reads do not support table hints, temporal clauses or column-renaming aliases')
                parts=[part.name for part in source.parts]
                if len(parts)==1 and parts[0] in self.table_mappings:logical=parts[0]
                elif len(parts)==2 and parts[0]==self.namespace and parts[1] in self.table_mappings:logical=parts[1]
                elif len(parts)==3:
                    matches=[key for key,value in self.table_mappings.items() if parts==[value['database'],value['schema'],value['table']]]
                    if len(matches)!=1:raise QueryError('The database/schema/table is not registered in this source')
                    logical=matches[0]
                else:raise QueryError('Use a registered table ID or an exact database.schema.table name; linked servers are not allowed')
                if logical not in tables or logical not in self.allowed:raise QueryError('SQL reads outside the confirmed mapped tables')
                resolved[alias]=(logical,parts,source.alias)
                source.set('this',exp.to_identifier(logical,quoted=True));source.set('catalog',None)
                source.set('db',exp.to_identifier(self.namespace,quoted=True))
                # Preserve original unaliased table-qualified columns (Customers.x).
                source.set('alias',exp.TableAlias(this=exp.to_identifier(alias,quoted=True)))
            for column in scope.columns:
                if not column.db and not column.catalog:continue
                matches=[(alias,info) for alias,info in resolved.items() if not info[2] and [p.name for p in column.parts[:-1]]==info[1]]
                if len(matches)!=1:raise QueryError('Qualified column is outside its registered source scope; use explicit SQL aliases')
                column.set('table',exp.to_identifier(matches[0][0],quoted=True));column.set('db',None);column.set('catalog',None)
        # Shared parser, function restrictions, bound values, projections and RLS
        # run on logical IDs. Only trusted physical references are expanded below.
        normalized=tree.sql(dialect='tsql',comments=False)
        rendered,referenced=WarehouseDatabase.checked_sql(self,normalized,parameters,tables)
        final=sqlglot.parse_one(rendered,read='tsql')
        for table in final.find_all(exp.Table):
            if table.db!=self.namespace:continue
            if table.catalog or table.name not in self.table_mappings:raise QueryError('Unregistered mapped execution dependency')
            target=self.table_mappings[table.name]
            table.set('this',exp.to_identifier(target['table'],quoted=True));table.set('db',exp.to_identifier(target['schema'],quoted=True));table.set('catalog',exp.to_identifier(target['database'],quoted=True))
        # T-SQL's native generator preserves :name bound values here.
        from sqlglot.dialects import Dialect
        dialect=Dialect.get_or_raise('tsql');generator=dialect.generator_class
        class NamedBinds(generator):
            TRANSFORMS={**generator.TRANSFORMS,exp.Placeholder:lambda self,node:':'+node.name}
        effective=NamedBinds(dialect=dialect,comments=False).generate(final)
        if len(effective)>200000:raise QueryError('Mapped query exceeds the expanded SQL budget; narrow the query')
        return effective,referenced

    def plan(self,sql,parameters,tables):
        return {**super().plan(sql,parameters,tables),
            'read_scope':'One SQL Server connection reads only explicitly registered database.schema.table objects on the same instance, with protected projections, configured row filters and source login grants. Linked servers are not permitted.'}
