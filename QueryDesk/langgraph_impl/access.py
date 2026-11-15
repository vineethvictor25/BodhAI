"""Request-scoped data views: table permissions and PII exclusions before SQL/LLM use."""
import json
import secrets
import sqlite3
from copy import copy,deepcopy

from .pii import field_aad
from .storage import Database,QueryError
from .value_codec import display
from .value_codec import dumps
from .pii_bounds import PIIBudget,PIIBoundError
from .connector_config import WAREHOUSE_DIALECTS


def filtered_schema(schema,denied):
    result=deepcopy(schema)
    for table in result["tables"]:
        excluded=set(denied.get(table["table"],[]))
        table["columns"]=[column for column in table["columns"] if column["name"] not in excluded]
        table["foreign_keys"]=[link for link in table["foreign_keys"] if link["column"] not in excluded and
            link["references_column"] not in denied.get(link["references_table"],[])]
    return result


class AccessView:
    def __init__(self,database,allowed,pii,can_view,policy,source,check,row_scope=None):
        self.base=database
        self.allowed=set(allowed) & database.allowed
        if not self.allowed:
            raise PermissionError("No tables are available in your assigned domains")
        self.pii,self.can_view,self.policy,self.source,self._check=pii,can_view,policy,source,check
        self.row_scope=row_scope
        self.denied={} if can_view else pii
        self.row_limit,self.timeout=database.row_limit,database.timeout

    def check(self):
        from .cancellation import check
        check()
        self._check()
        if self.row_scope:self.row_scope.check()

    def row_security(self,tables):
        return self.row_scope.describe(tables) if self.row_scope else {'enabled':False,'filters':[]}

    def annotate_rows(self,result,tables):
        self.check()
        if self.row_scope and self.row_scope.enabled and not self.row_scope.bypass:
            result={**result,'access_note':'Results include only rows permitted by your configured row-level access restrictions.'}
        return result

    def catalog(self):
        self.check()
        return {**self.base.catalog(),"tables":sorted(self.allowed)}

    def table_options(self,tables):
        self.check()
        if not set(tables)<=self.allowed:raise QueryError('The requested tables are outside your assigned domains')
        describe=getattr(self.base,'table_options',None)
        return describe(tables) if callable(describe) else None

    def schema(self,tables):
        self.check()
        if not set(tables)<=self.allowed:
            raise QueryError("The requested tables are outside your assigned domains")
        result=filtered_schema(self.base.schema(tables),self.denied)
        for table in result["tables"]:
            table["foreign_keys"]=[link for link in table["foreign_keys"] if link["references_table"] in self.allowed]
        return result

    def model_rows(self,result):
        """Keep authorized plaintext data cells out of response-generation prompts.

        All non-NULL cells are tokenized conservatively; computed/aliased PII cannot bypass it.
        Tokens are restored locally only for the already-authorized owner response.
        """
        self.check()
        if not self.can_view:
            return result,lambda value:value
        mapping={}
        reverse={}
        rows=deepcopy(result)
        for row in rows["rows"]:
            for index,value in enumerate(row):
                if value is not None:
                    identity=(type(value).__name__,str(value))
                    if identity not in reverse:
                        reverse[identity]="VALUE_"+secrets.token_hex(12)
                    token=reverse[identity]
                    mapping[token]=display(value)
                    row[index]=token
        def restore(text):
            for token,value in mapping.items():
                text=text.replace(token,value)
            return text
        return rows,restore


class SQLiteAccess(AccessView,Database):
    def execution_capabilities(self):
        return {'dialect':'sqlite','protected_direct_reads':True,'local_pii_decryption':True,
            'selective_local_copy':True,
            'bounded_local_pii':True,
            'enforcement':'derived_projections_and_sqlite_authorizer'}

    def plan(self,sql,parameters,tables):
        from .planning import sqlite_plan
        from .sqlite_direct import prepare
        self.check()
        metadata=sqlite_plan(self,sql,parameters,tables)
        pii_needed=self.can_view and any(set(columns)&set(self.pii.get(table,[])) for table,columns in metadata['referenced_columns'].items())
        if pii_needed:
            from .sqlite_copy import prepare as prepare_copy
            if not callable(getattr(self.policy.cipher,'decrypt_bounded',None)):raise QueryError('The configured cipher has no bounded local-decryption capability')
            plan={**metadata,**prepare_copy(self,sql,parameters,tables,metadata),'execution_mode':'materialized',
                'pii_limits':self.policy.processing_limits.payload(),
                'read_scope':f'This query references classified PII. Only required columns/keys and permitted rows are copied locally. The approved preparation cap is {self.policy.processing_limits.max_rows:,} rows / {self.policy.processing_limits.max_copy_bytes:,} plaintext bytes, with additional cell, input, decryption and time limits shown below.'}
        else:
            plan={**metadata,**prepare(self,sql,parameters,tables),
                'read_scope':'Read-only SQL runs directly on the source through protected column projections and row filters. No business rows are copied into a local database.'}
        self._prepared_plan=(self.plan_key(sql,parameters,tables),plan)
        # Physical dependency schemas remain server-private, including policy-only helpers.
        return {key:value for key,value in plan.items() if key not in {'physical_schema','copy_plan'}}

    def plan_key(self,sql,parameters,tables):
        return dumps([sql,parameters,sorted(tables),self.row_limit,self.timeout,self.can_view,self.pii,
            getattr(self.row_scope,'signature',None),self.policy.signature,self.policy.processing_limits.payload()],sort_keys=True)

    def open_connection(self):
        self.check()
        copy_plan=getattr(self,'_copy_plan',None)
        if copy_plan is None:raise QueryError('A prepared local-copy plan is required')
        target=sqlite3.connect(":memory:")
        budget=PIIBudget(self._pii_limits)
        quote=lambda name:'"'+name.replace('"','""')+'"'
        try:
            target.execute('PRAGMA temp_store=MEMORY')
            with self.base.connection() as source:
                # Only generated, policy-bound source SQL uses the expanded budget.
                source.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,200000)
                source.set_progress_handler(lambda:int(budget.clock()>=budget.deadline),500)
                source.execute('BEGIN')
                regular={row[1] for row in source.execute('PRAGMA main.table_list') if row[0]=='main' and row[2]=='table'}
                for expected in copy_plan['physical_schema']['tables']:
                    physical=expected['table']
                    actual=[(c[1],c[2],bool(c[5]),not(c[3] or c[5])) for c in source.execute('PRAGMA main.table_info('+quote(physical)+')')]
                    if physical not in regular or actual!=[(c['name'],c['type'],c['primary_key'],c['nullable']) for c in expected['columns']]:
                        raise QueryError('Source dependency schema changed; review the copy plan again')
                for table in sorted(self.allowed):
                    budget.check_time()
                    self.check()
                    columns=list(source.execute("PRAGMA table_info("+quote(table)+")"))
                    expected=next(item for item in copy_plan['schema']['tables'] if item['table']==table)
                    actual=[(c[1],c[2],bool(c[5]),not(c[3] or c[5])) for c in columns]
                    if table not in regular or actual!=[(c['name'],c['type'],c['primary_key'],c['nullable']) for c in expected['columns']]:
                        raise QueryError('Source schema changed; review the copy plan again')
                    names=[column[1] for column in columns if column[1] not in self.denied.get(table,[])]
                    if not names:
                        raise QueryError("No accessible columns remain in a selected table")
                    primary=[column[1] for column in sorted(columns,key=lambda c:c[5]) if column[5]]
                    definitions=",".join(quote(name) for name in names)
                    # No original DDL is executed; source defaults/triggers/functions are excluded.
                    target.execute("CREATE TABLE "+quote(table)+" ("+definitions+")")
                    step=next(item for item in copy_plan['steps'] if item['table']==table)
                    if not step['source_sql']:continue
                    names=step['columns']
                    cursor=source.execute(step['source_sql'],step['parameters'])
                    insert="INSERT INTO "+quote(table)+' ('+','.join(quote(name) for name in names)+") VALUES ("+",".join("?" for _ in names)+")"
                    for raw in cursor:
                        budget.before_row(raw)
                        if budget.rows%1000==0:self.check()
                        row=dict(zip(names,raw))
                        if self.can_view:
                            identity=[row[key] for key in primary] if step['decrypt_columns'] else []
                            for column in step['decrypt_columns']:
                                if column in row:
                                    budget.before_decrypt()
                                    row[column]=self.policy.cipher.decrypt_bounded(row[column],field_aad(self.source,table,column,identity),budget.limits)
                        values=[row[name] for name in names]
                        budget.copy_row(values)
                        target.execute(insert,values)
            budget.check_time();self.check();target.commit()
            return target
        except Exception:
            target.close()
            budget.check_time()
            raise

    def validate(self,sql,parameters,tables):
        return self.plan(sql,parameters,tables)

    def execute(self,sql,parameters,tables):
        self.check()
        if self.row_scope and self.row_scope.enabled and any(key.lower().startswith('__rls_') for key in parameters):raise QueryError('Row-policy parameter names are reserved')
        key=self.plan_key(sql,parameters,tables)
        cached=getattr(self,'_prepared_plan',None)
        if not cached or cached[0]!=key:self.plan(sql,parameters,tables)
        plan=self._prepared_plan[1]
        if plan['execution_mode']=='direct':
            from .sqlite_direct import execute
            return self.annotate_rows(execute(self,plan),tables)
        # Materialize only when classified PII handling is required.
        scoped=copy(self)
        scoped.allowed=set(tables)&self.allowed
        scoped._copy_plan=plan['copy_plan']
        scoped._pii_limits=self.policy.processing_limits
        return self.annotate_rows({**Database.execute(scoped,sql,parameters,tables),'execution_mode':'materialized'},tables)


class WarehouseAccess(AccessView):
    def execution_capabilities(self):
        return {'dialect':self.base.kind,'protected_direct_reads':True,'local_pii_decryption':False,
            'same_instance_cross_database':hasattr(self.base,'table_mappings'),
            'enforcement':'source_side_derived_projections',
            'tls':{'encryption_required':getattr(self.base,'tls12_enabled',True) or self.base.kind in {'snowflake','bigquery'},'certificate_verification_when_encrypted':True,
                'tls12_enforced':getattr(self.base,'tls12_enabled',True),'minimum_version':'TLSv1.2' if getattr(self.base,'tls12_enabled',True) else 'driver_default'},
            'write_protection':'read_only_transaction_and_database_grants' if self.base.kind in {'postgresql','mysql','oracle','redshift'} else 'database_grants_required',
            'live_runtime_verified':False}

    def plan(self,sql,parameters,tables):
        return self.adapter().plan(sql,parameters,tables)

    def adapter(self):
        self.check()
        adapter=copy(self.base)
        adapter.allowed=set(self.allowed)
        adapter.security_projection={table:[c["name"] for c in info["columns"]] for info in self.schema(sorted(self.allowed))["tables"] for table in [info["table"]]}
        if self.row_scope and self.row_scope.enabled:
            adapter.security_row_filters={table:self.row_scope.filter(table,adapter.kind,adapter.namespace) for table in self.allowed}
        return adapter

    def validate(self,sql,parameters,tables):
        return self.adapter().validate(sql,parameters,tables)

    def execute(self,sql,parameters,tables):
        return self.annotate_rows(self.adapter().execute(sql,parameters,tables),tables)


def protected_database(database,allowed,pii,can_view,policy,source,check,row_scope=None):
    if isinstance(database,Database):cls=SQLiteAccess
    else:
        from .warehouse import WarehouseDatabase
        if isinstance(database,WarehouseDatabase) and database.kind in WAREHOUSE_DIALECTS:cls=WarehouseAccess
        else:raise QueryError('This connector has no verified protected-read adapter')
    return cls(database,allowed,pii,can_view,policy,source,check,row_scope)
