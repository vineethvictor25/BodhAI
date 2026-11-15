"""BigQuery Standard SQL with dataset scope, ADC and explicit billed-byte limits."""
import re
import os
from itertools import islice
from decimal import Decimal

from .storage import QueryError
from .warehouse import WarehouseDatabase
from .tls_policy import secure_http_session


class BigQueryDatabase(WarehouseDatabase):
    def __init__(self,address,allowed_tables,schema=None,row_limit=100,timeout=3,credentials_file=None,tls12_enabled=True):
        self.kind,self.row_limit,self.timeout='bigquery',row_limit,timeout
        if type(tls12_enabled) is not bool:raise QueryError('Invalid TLS enforcement setting')
        self.tls12_enabled=tls12_enabled
        self.client=None
        self._http_sessions=()
        try:
            from sqlalchemy.engine import make_url
            from google.cloud import bigquery
            import google.auth
            if os.environ.get('BIGQUERY_EMULATOR_HOST'):raise ValueError('Plaintext BigQuery emulators are not permitted')
            url=make_url(address)
            if url.drivername!='bigquery' or url.username or url.password or url.port or set(url.query)-{'location','maximum_bytes_billed'}:
                raise ValueError('Unsupported BigQuery URL')
            if not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]',url.host or '') or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',url.database or ''):
                raise ValueError('Explicit project and dataset required')
            if schema and schema!=url.database:raise ValueError('Dataset must match configured schema')
            if not 1<=row_limit<=1000 or not 0<timeout<=60:raise ValueError('Invalid query limits')
            self.project,self.namespace=url.host,url.database
            self.location=url.query.get('location')
            if not self.location or not re.fullmatch(r'[A-Za-z0-9-]{2,40}',self.location):raise ValueError('Explicit BigQuery location required')
            self.maximum_bytes_billed=int(url.query.get('maximum_bytes_billed','1000000000'))
            if not 1<=self.maximum_bytes_billed<=10**12:raise ValueError('Invalid BigQuery billed-byte limit')
            credentials=None
            if credentials_file:
                from google.oauth2 import service_account
                credentials=service_account.Credentials.from_service_account_file(credentials_file)
            if credentials is None:credentials,_=google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
            self._http_sessions=secure_http_session(credentials,tls12_enabled)
            self.client=bigquery.Client(project=self.project,credentials=credentials,location=self.location,_http=self._http_sessions[0])
            self.allowed=set(allowed_tables)
            if not self.allowed or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',table) for table in self.allowed):
                raise ValueError('Regular table names required')
            self.schema(sorted(self.allowed)) # metadata only; no query jobs or table data
        except Exception:
            if self.client:self.client.close()
            for session in self._http_sessions:session.close()
            raise QueryError('Cannot open BigQuery. Check optional dependencies, ADC identity, project, dataset, location and read-only table permissions.') from None

    def catalog(self):
        return {'dialect':'bigquery','project':self.project,'default_schema':self.namespace,'tables':sorted(self.allowed)}

    def schema(self,tables):
        if not tables or not set(tables)<=self.allowed:raise QueryError('Choose permitted BigQuery tables')
        metadata=[]
        try:
            for name in sorted(set(tables)):
                table=self.client.get_table(f'{self.project}.{self.namespace}.{name}',timeout=5,retry=None)
                if table.table_type!='TABLE' or table.external_data_configuration is not None:
                    raise QueryError('Only regular native BigQuery tables are supported')
                if any(field.mode=='REPEATED' or field.field_type in {'RECORD','STRUCT'} for field in table.schema):
                    raise QueryError('Nested/repeated BigQuery columns require a separate reviewed adapter extension')
                constraints=table.to_api_repr().get('tableConstraints',{})
                primary=set(constraints.get('primaryKey',{}).get('columns',[]))
                columns=[{'name':field.name,'type':field.field_type,'primary_key':field.name in primary,'nullable':field.mode!='REQUIRED'} for field in table.schema]
                links=[]
                for key in constraints.get('foreignKeys',[]):
                    target=key['referencedTable']
                    if target['projectId']==self.project and target['datasetId']==self.namespace and target['tableId'] in self.allowed:
                        links.extend({'column':pair['referencingColumn'],'references_table':target['tableId'],'references_column':pair['referencedColumn']} for pair in key['columnReferences'])
                if hasattr(self,'security_projection'):
                    columns=[column for column in columns if column['name'] in self.security_projection.get(name,[])]
                    links=[link for link in links if link['column'] in self.security_projection.get(name,[]) and link['references_column'] in self.security_projection.get(link['references_table'],[])]
                metadata.append({'table':name,'columns':columns,'foreign_keys':links})
        except QueryError:raise
        except Exception:raise QueryError('BigQuery metadata access failed. Check table grants and dataset scope.') from None
        return {'dialect':'bigquery','project':self.project,'default_schema':self.namespace,'tables':metadata}

    def plan(self,sql,parameters,tables):
        plan=super().plan(sql,parameters,tables)
        return {**plan,'resource_limits':{'maximum_bytes_billed':self.maximum_bytes_billed,'location':self.location},
            'read_scope':plan['read_scope']+f' BigQuery location: {self.location}; maximum billed bytes: {self.maximum_bytes_billed:,}. Native temporary query results may be stored by BigQuery.'}

    def execute(self,sql,parameters,tables):
        from google.cloud import bigquery
        rendered,referenced=self.checked_sql(sql,parameters,tables)
        parameters=self.bound_parameters(parameters,referenced)
        typed=[]
        for key,value in parameters.items():
            kind='BOOL' if type(value) is bool else 'INT64' if type(value) is int else 'FLOAT64' if type(value) is float else 'BIGNUMERIC' if isinstance(value,Decimal) else 'STRING'
            typed.append(bigquery.ScalarQueryParameter(key,kind,value))
        config=bigquery.QueryJobConfig(use_legacy_sql=False,maximum_bytes_billed=self.maximum_bytes_billed,
            job_timeout_ms=max(1,int(self.timeout*1000)),default_dataset=f'{self.project}.{self.namespace}',query_parameters=typed)
        job=None
        try:
            job=self.client.query(rendered,job_config=config,location=self.location,timeout=self.timeout,retry=None,job_retry=None)
            from .cancellation import interruptible
            with interruptible(lambda: job.cancel(timeout=2,retry=None)):
                result=job.result(timeout=self.timeout,max_results=self.row_limit+1,page_size=self.row_limit+1,retry=None,job_retry=None)
                raw=list(islice(result,self.row_limit+1))
            return self.format_remote_result([field.name for field in result.schema],raw,rendered,parameters)
        except Exception:
            if job:
                try:job.cancel(timeout=2,retry=None)
                except Exception:pass
            raise QueryError('BigQuery execution failed or exceeded its query/cost limits. Check permissions, location and query settings.') from None

    def close(self):
        self.client.close()
        for session in self._http_sessions:session.close()
