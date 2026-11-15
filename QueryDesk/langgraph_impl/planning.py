"""Metadata-only SQLite compilation and canonical human-review manifests."""
import hashlib
import sqlite3

from .value_codec import dumps


def sqlite_plan(database,sql,parameters,tables):
    from .storage import Database
    metadata=database.schema(tables)
    mirror=object.__new__(Database)
    mirror.allowed=set(tables)
    mirror.row_limit,mirror.timeout=database.row_limit,database.timeout
    def connection():
        conn=sqlite3.connect(':memory:')
        quote=lambda name:'"'+name.replace('"','""')+'"'
        try:
            for table in metadata['tables']:
                columns=','.join(quote(column['name']) for column in table['columns'])
                if not columns:raise ValueError('No accessible columns remain in a selected table')
                conn.execute('CREATE TABLE '+quote(table['table'])+' ('+columns+')')
            return conn
        except Exception:
            conn.close();raise
    mirror.open_connection=connection
    return {**mirror.validate(sql,parameters,tables),'effective_sql':sql,'metadata_only':True}


def manifest(database,candidate,tables):
    """Bind displayed SQL, security projection, values and resource limits together."""
    tables=sorted(set(tables))
    plan=database.plan(candidate.sql,dict(candidate.parameters),tables)
    payload={'sql':plan['effective_sql'],'parameters':plan.get('effective_parameters',dict(candidate.parameters)),'tables':tables,
        'schema':database.schema(tables),'source':getattr(database,'source','operator'),
        'policy_version':getattr(getattr(database,'policy',None),'signature','operator'),
        'row_limit':database.row_limit,'execution_timeout_seconds':database.timeout,
        'read_scope':plan.get('read_scope','The read-only SELECT runs against the selected source.'),
        'execution_mode':plan.get('execution_mode','operator'),
        'checks':'Metadata planning and model review are advisory. Approval permits execution-time compilation and data reads.'}
    if 'source_schema_fingerprint' in plan:payload['source_schema_fingerprint']=plan['source_schema_fingerprint']
    if 'copy_scope' in plan:payload['copy_scope']=plan['copy_scope']
    if 'pii_limits' in plan:payload['pii_limits']=plan['pii_limits']
    if 'resource_limits' in plan:payload['resource_limits']=plan['resource_limits']
    if hasattr(database,'execution_capabilities'):payload['adapter_capabilities']=database.execution_capabilities()
    if hasattr(database,'row_security'):payload['row_security']=database.row_security(tables)
    return {**payload,'fingerprint':hashlib.sha256(dumps(payload,sort_keys=True).encode()).hexdigest()}
