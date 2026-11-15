"""Metadata-only selective-copy plans for SQLite's local PII path."""
import hashlib
import json

import sqlglot
from sqlglot import exp

from .storage import QueryError


def prepare(view,sql,parameters,tables,metadata):
    schema=view.base.schema(tables)
    quote=lambda name:'"'+name.replace('"','""')+'"'
    by_name={table['table']:table for table in schema['tables']}
    reads=metadata['referenced_columns']
    dependencies=set(by_name)
    pushed={}
    try:tree=sqlglot.parse_one(sql,read='sqlite')
    except sqlglot.errors.ParseError:tree=None
    # Never move predicates across joins, subqueries, CTEs, or pseudo-rowid semantics.
    if isinstance(tree,exp.Select) and len(list(tree.find_all(exp.Select)))==1 and not tree.args.get('with_') and not tree.args.get('joins') and not list(tree.find_all(exp.Subquery)):
        source=tree.args.get('from_')
        source=source.this if source else None
        matches=[name for name in by_name if isinstance(source,exp.Table) and name.casefold()==source.name.casefold()]
        if len(matches)==1 and not source.catalog and source.db in {'','main'}:
            table=matches[0]
            names={column['name'].casefold():column['name'] for column in by_name[table]['columns']}
            unknown=set(reads.get(table,[]))-set(names.values())
            where=tree.args.get('where')
            def leaves(node):
                if isinstance(node,exp.Paren):return leaves(node.this)
                if isinstance(node,exp.And):return leaves(node.this)+leaves(node.expression)
                return [node]
            if where and not unknown:
                pushed[table]=[node for node in leaves(where.this)]
    steps=[]
    for table in schema['tables']:
        name=table['table']
        visible=[c['name'] for c in table['columns'] if c['name'] not in view.denied.get(name,[])]
        primary=[c['name'] for c in table['columns'] if c['primary_key']]
        used=set(reads.get(name,[]))
        decrypt=used & set(view.pii.get(name,[])) if view.can_view else set()
        if decrypt and not primary:raise QueryError('PII tables require a stable primary key')
        selected=used | (set(primary) if decrypt else set())
        columns=[column for column in visible if column in selected]
        marker=name in reads and not columns
        if marker:columns=visible[:1]
        if name in reads and not columns:raise QueryError('No accessible columns remain')
        row_filter=view.row_scope.filter(name,'sqlite','main') if view.row_scope else {'sql':'1=1','parameters':{}}
        for helper in sqlglot.parse_one(row_filter['sql'],read='sqlite').find_all(exp.Table):
            if helper.db!='main' or helper.name not in view.base.allowed:raise QueryError('Invalid row-policy dependency')
            dependencies.add(helper.name)
        bound=dict(row_filter['parameters']);conditions=[]
        names={c.casefold():c for c in visible}
        def value(node):
            if isinstance(node,exp.Placeholder):
                if node.name not in parameters:raise ValueError()
                result=parameters[node.name]
            elif isinstance(node,exp.Null):result=None
            elif isinstance(node,exp.Literal) and node.is_string:result=node.this
            elif isinstance(node,exp.Literal) and node.is_int:result=int(node.this)
            elif isinstance(node,exp.Neg) and isinstance(node.this,exp.Literal) and node.this.is_int:result=-int(node.this.this)
            else:raise ValueError()
            if result is not None and not (type(result) is str or type(result) is int and -(2**63)<=result<2**63):raise ValueError()
            return result
        operators={exp.EQ:'=',exp.NEQ:'!=',exp.GT:'>',exp.GTE:'>=',exp.LT:'<',exp.LTE:'<=',exp.Is:'IS'}
        for predicate in pushed.get(name,[]):
            if type(predicate) not in set(operators)|{exp.In,exp.Between}:continue
            column=predicate.this
            if not isinstance(column,exp.Column) or column.catalog or column.db not in {'','main'}:continue
            actual=names.get(column.name.casefold())
            if actual is None or actual in view.pii.get(name,[]):continue
            if column.table and column.table.casefold()!=source.alias_or_name.casefold():continue
            try:
                if isinstance(predicate,exp.In):
                    if predicate.args.get('query') or not 1<=len(predicate.expressions)<=100:continue
                    values=[value(item) for item in predicate.expressions]
                elif isinstance(predicate,exp.Between):values=[value(predicate.args['low']),value(predicate.args['high'])]
                else:
                    if isinstance(predicate,exp.Is) and not isinstance(predicate.expression,exp.Null):continue
                    values=[value(predicate.expression)]
            except (ValueError,KeyError):continue
            binds=[]
            for item in values:
                key='__copy_'+str(len(bound));bound[key]=item;binds.append(':'+key)
            # Match the existing untyped local table and its BINARY collation exactly.
            # Unary + removes source-column affinity without converting stored values.
            lhs='(+'+quote('__rls_b')+'.'+quote(actual)+' COLLATE BINARY)'
            if isinstance(predicate,exp.In):expression=lhs+' IN ('+','.join(binds)+')'
            elif isinstance(predicate,exp.Between):expression=lhs+' BETWEEN '+binds[0]+' AND '+binds[1]
            else:expression=lhs+' '+operators[type(predicate)]+' '+binds[0]
            conditions.append(expression)
        predicate=' AND '.join('('+part+')' for part in [row_filter['sql']]+conditions)
        projection=','.join(('NULL AS '+quote(column)) if marker else quote('__rls_b')+'.'+quote(column) for column in columns)
        source_sql=('SELECT '+projection+' FROM "main".'+quote(name)+' AS '+quote('__rls_b')+' WHERE '+predicate) if name in reads else None
        if source_sql and (len(source_sql)>200000 or len(bound)>1000):raise QueryError('Local copy plan exceeds its SQL/parameter budget')
        steps.append({'table':name,'columns':columns,'decrypt_columns':sorted(decrypt),'primary_key':primary,
            'row_marker_only':marker,'source_sql':source_sql,'parameters':bound if source_sql else {}})
    physical_schema=view.base.schema(sorted(dependencies))
    fingerprint=hashlib.sha256(json.dumps(physical_schema,sort_keys=True).encode()).hexdigest()
    public=[{key:step[key] for key in ('table','columns','decrypt_columns','row_marker_only','source_sql','parameters')} for step in steps if step['source_sql']]
    return {'copy_plan':{'schema':schema,'physical_schema':physical_schema,'steps':steps},'copy_scope':public,'source_schema_fingerprint':fingerprint}
