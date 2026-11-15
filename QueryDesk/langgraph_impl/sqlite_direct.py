"""SQLite-specific compiler/executor behind the shared access and approval contracts."""
import hashlib
import json
import sqlite3
import time

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope,Scope
from sqlglot.errors import ErrorLevel,UnsupportedError

from .storage import QueryError,SAFE_FUNCTIONS


def prepare(view,sql,parameters,tables):
    view.inputs(sql,parameters,tables)
    if any(key.lower().startswith('__rls_') for key in parameters):raise QueryError('Row-policy parameter names are reserved')
    try:statements=sqlglot.parse(sql,read='sqlite')
    except sqlglot.errors.ParseError:raise QueryError('SQL is unsupported by the protected SQLite compiler') from None
    if len(statements)!=1 or not isinstance(statements[0],(exp.Select,exp.Union,exp.Intersect,exp.Except)):
        raise QueryError('Only one protected SELECT is permitted')
    tree=statements[0]
    banned={'Insert','Update','Delete','Merge','Create','Drop','Alter','Command','Copy','Into','Lock','Transaction',
        'Commit','Rollback','Set','Use','Grant','Revoke','Attach','Detach','Execute','Pragma','Hint','Parameter'}
    if any(type(node).__name__ in banned for node in tree.walk()):raise QueryError('Unsupported operation in protected SQLite SQL')
    physical=set();bound=dict(parameters)
    visible={table['table']:[c['name'] for c in table['columns']] for table in view.schema(tables)['tables']}
    for scope in list(traverse_scope(tree)):
        for source in scope.sources.values():
            if isinstance(source,Scope):continue
            if not isinstance(source,exp.Table) or not isinstance(source.this,exp.Identifier):raise QueryError('Table functions are not permitted')
            name=source.name
            if source.catalog or source.db not in {'','main'} or name not in tables:raise QueryError('SQL reads outside confirmed tables')
            columns=visible[name]
            if not columns:raise QueryError('No permitted columns remain')
            physical.add(name)
            row_filter=view.row_scope.filter(name,'sqlite','main') if view.row_scope else {'sql':'1=1','parameters':{}}
            bound.update(row_filter['parameters'])
            base=exp.Table(this=exp.to_identifier(name,quoted=True),db=exp.to_identifier('main',quoted=True)).as_('__rls_b',quoted=True)
            projection=exp.select(*[exp.column(column,table='__rls_b',quoted=True) for column in columns]).from_(base)
            predicate=sqlglot.parse_one(row_filter['sql'],read='sqlite')
            # Policy helper references are trusted, schema-qualified physical dependencies.
            for helper in predicate.find_all(exp.Table):
                if helper.db!='main' or helper.name not in view.base.allowed:raise QueryError('Invalid row-policy dependency')
                physical.add(helper.name)
            projection=projection.where(predicate)
            alias=source.alias_or_name
            source.replace(exp.Subquery(this=projection,alias=exp.TableAlias(this=exp.to_identifier(alias,quoted=True))))
        for column in scope.columns:
            if column.catalog or column.db not in {'','main'}:raise QueryError('Unsupported column namespace')
            if column.db:column.set('db',None)
    # Preserve named binds; never interpolate user or policy values into SQL.
    from sqlglot.dialects.sqlite import SQLite
    class NamedBinds(SQLite.Generator):
        TRANSFORMS={**SQLite.Generator.TRANSFORMS,exp.Placeholder:lambda self,node:':'+node.name}
        def tablealias_sql(self,expression):
            # SQLite supports CTE column names, not derived-table column aliases.
            if isinstance(expression.parent,exp.CTE):
                names=self.expressions(expression,key='columns',flat=True)
                return self.sql(expression,'this')+('('+names+')' if names else '')
            return super().tablealias_sql(expression)
    try:rendered=NamedBinds(comments=False,unsupported_level=ErrorLevel.RAISE).generate(tree)
    except UnsupportedError:raise QueryError('SQL cannot be safely rendered for direct SQLite reads') from None
    if len(rendered)>200000 or len(bound)>1000:raise QueryError('Protected query exceeds its compilation budget')
    schema=view.base.schema(sorted(physical)) if physical else {'tables':[]}
    fingerprint=hashlib.sha256(json.dumps(schema,sort_keys=True).encode()).hexdigest()
    return {'effective_sql':rendered,'effective_parameters':bound,'execution_mode':'direct',
        'source_schema_fingerprint':fingerprint,'physical_schema':schema}


def execute(view,plan):
    view.check()
    conn=view.base.open_connection()
    from .cancellation import interruptible
    try:
        with interruptible(conn.interrupt):
            conn.execute('PRAGMA query_only=1');conn.execute('PRAGMA trusted_schema=0');conn.execute('BEGIN')
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,1_000_000)
            conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,200000)
            regular={row[1] for row in conn.execute('PRAGMA main.table_list') if row[0]=='main' and row[2]=='table' and not row[1].startswith('sqlite_')}
            columns={}
            quote=lambda name:'"'+name.replace('"','""')+'"'
            for table in plan['physical_schema']['tables']:
                name=table['table']
                if name not in regular:raise QueryError('Source table changed; review the query again')
                actual=[(r[1],r[2],bool(r[5]),not(r[3] or r[5])) for r in conn.execute('PRAGMA main.table_info('+quote(name)+')')]
                expected=[(c['name'],c['type'],c['primary_key'],c['nullable']) for c in table['columns']]
                if actual!=expected:raise QueryError('Source schema changed; review the query again')
                permitted={c['name'] for c in table['columns']}
                if not view.can_view:permitted-=set(view.pii.get(name,[]))
                columns[name]=permitted
            deadline=time.monotonic()+view.timeout
            from .cancellation import cancelled
            conn.set_progress_handler(lambda:int(time.monotonic()>=deadline or cancelled()),500)
            def authorize(operation,table,column,namespace,trigger):
                allowed=operation in {sqlite3.SQLITE_SELECT,sqlite3.SQLITE_RECURSIVE}
                if operation==sqlite3.SQLITE_READ:
                    allowed=namespace=='main' and table in columns and (not column or column in columns[table])
                elif operation==sqlite3.SQLITE_FUNCTION:allowed=(column or '').lower() in SAFE_FUNCTIONS
                return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
            conn.set_authorizer(authorize)
            view.check()
            sql,parameters=plan['effective_sql'],plan['effective_parameters']
            conn.execute('EXPLAIN QUERY PLAN '+sql,parameters).fetchall()
            cursor=conn.execute(sql,parameters)
            raw=cursor.fetchmany(view.row_limit+1)
            result=view.format_result([column[0] for column in cursor.description],raw)
            view.check()
            return {**result,'executed_sql':sql,'executed_parameters':parameters,'execution_mode':'direct'}
    except sqlite3.Error as exc:raise QueryError(str(exc)) from None
    finally:conn.close()
