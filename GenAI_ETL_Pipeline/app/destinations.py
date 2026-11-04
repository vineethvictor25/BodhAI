"""Validated deployment choices. Export plans never imply remote execution."""
import json
import re
from typing import Literal
from pydantic import Field,model_validator
from .models_base import StrictModel

CATALOG={
 'local':{'label':'Local workspace','warehouses':{'sqlite':'SQLite · executable local reference'}},
 'azure':{'label':'Microsoft Azure','warehouses':{'sqlserver':'Azure SQL / SQL Server','synapse':'Azure Synapse dedicated SQL pool','snowflake':'Snowflake on Azure','databricks':'Azure Databricks SQL'}},
 'aws':{'label':'Amazon Web Services','warehouses':{'redshift':'Amazon Redshift','snowflake':'Snowflake on AWS','databricks':'Databricks on AWS'}},
 'gcp':{'label':'Google Cloud','warehouses':{'bigquery':'BigQuery','snowflake':'Snowflake on Google Cloud','databricks':'Databricks on Google Cloud'}}}

# Values are non-secret identifiers. No password, token or connection-string fields.
CONFIG_FIELDS={
 'sqlite':[],
 'sqlserver':[('server','SQL Server hostname',True),('database','Database',True),('identity_ref','Managed identity / Key Vault credential reference',True),('staging_schema','Staging schema',False)],
 'synapse':[('server','Synapse SQL endpoint',True),('database','Dedicated SQL pool',True),('storage_uri','ADLS staging URI (abfss://)',True),('identity_ref','Managed identity reference',True)],
 'snowflake':[('account','Account identifier',True),('database','Database',True),('compute_warehouse','Compute warehouse',True),('role','Loading role',True),('stage','Approved external stage name',True),('identity_ref','Workload identity / secret-manager reference',True)],
 'bigquery':[('project_id','Google Cloud project',True),('location','Dataset location',True),('storage_uri','GCS staging prefix (gs://)',True),('identity_ref','Workload identity reference',True)],
 'redshift':[('endpoint','Cluster / Serverless endpoint',True),('database','Database',True),('storage_uri','S3 staging prefix (s3://)',True),('iam_role_arn','COPY IAM role ARN',True),('identity_ref','Workload identity reference',True)],
 'databricks':[('workspace_url','HTTPS workspace URL',True),('sql_warehouse_id','SQL warehouse ID',True),('external_location','Unity Catalog external location',True),('identity_ref','OAuth / managed identity reference',True)]}


def validate_settings(warehouse,settings):
    allowed={f[0] for f in CONFIG_FIELDS[warehouse]}
    if set(settings)-allowed:raise ValueError('Unknown destination setting; credentials must be external references')
    for key,value in settings.items():
        if not isinstance(value,str) or len(value)>500 or any(c in value for c in ('\n','\r',';','\x00')):raise ValueError('Invalid destination setting')
        if key=='identity_ref' and value and not re.fullmatch(r'(?:managed-identity|workload-identity|keyvault|secretsmanager|secretmanager)://[A-Za-z0-9_./:@-]+',value):raise ValueError('Identity must be an external reference such as managed-identity://system or keyvault://vault/secret-name')
        if key=='storage_uri' and value:
            prefix={'bigquery':'gs://','redshift':'s3://','synapse':'abfss://'}[warehouse]
            if not value.startswith(prefix) or any(c in value for c in ('?','#','@')) and warehouse!='synapse':raise ValueError('Use an unsigned staging URI for the selected destination')
            if '?' in value or '#' in value:raise ValueError('Signed URLs and embedded credentials are not accepted')
        elif key=='workspace_url' and value:
            from urllib.parse import urlparse
            u=urlparse(value)
            if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:raise ValueError('Workspace requires an HTTPS URL without credentials')
        elif value and not re.fullmatch(r'[A-Za-z0-9_./:@{}=-]+',value):raise ValueError('Setting must be a non-secret identifier or external credential reference')
    return settings


class Destination(StrictModel):
    cloud:Literal['local','azure','aws','gcp']='local'
    warehouse:Literal['sqlite','sqlserver','synapse','snowflake','databricks','redshift','bigquery']='sqlite'
    namespace:str=Field(default='analytics',pattern=r'^[a-zA-Z_][a-zA-Z0-9_]{0,62}$')
    catalog:str=Field(default='',pattern=r'^$|^[a-zA-Z_][a-zA-Z0-9_-]{0,100}$')
    settings:dict[str,str]=Field(default_factory=dict,max_length=12)

    @model_validator(mode='after')
    def valid_pair(self):
        if self.warehouse not in CATALOG[self.cloud]['warehouses']:raise ValueError('Warehouse is not available for the selected cloud')
        validate_settings(self.warehouse,self.settings)
        return self


def public_catalog():
    return {cloud:{'label':entry['label'],'warehouses':[{'id':warehouse,'label':label,'execution':'local' if cloud=='local' else 'export_plan_only','live_connector_available':cloud=='local','configuration_fields':[{'name':f[0],'label':f[1],'required':f[2]} for f in CONFIG_FIELDS[warehouse]],'load_modes':['upsert','append','full_refresh','partition_overwrite','scd2']} for warehouse,label in entry['warehouses'].items()]} for cloud,entry in CATALOG.items()}


def deployment_plan(spec):
    d=spec.destination
    schemas={'bronze':d.namespace+'_bronze','silver':d.namespace+'_silver','gold':d.namespace+'_gold'}
    stage=f"{d.settings.get('staging_schema') or schemas['silver']}.{spec.target}_stage"
    missing=[f[0] for f in CONFIG_FIELDS[d.warehouse] if f[2] and not d.settings.get(f[0])]
    load_behavior={'upsert':'Stage, then MERGE on the business key; preserve unmatched target rows','append':'Stage, reject keys already in target, then INSERT','full_refresh':'Build a full replacement and promote atomically; never truncate before validation','partition_overwrite':'Replace only explicitly declared partitions after collision checks','scd2':'Detect tracked-column hash changes, expire old current rows, insert a new version; require one current version per key'}[spec.load.mode]
    return {'destination':d.model_dump(),'missing_configuration':missing,'configuration_complete':not missing,'load_behavior':load_behavior,'business':spec.business.model_dump() if spec.business else None,'target':spec.target,'schemas':schemas,'staging_table':stage,'grain':spec.business.grain if spec.business else None,'primary_key':spec.primary_key,'load_mode':spec.load.mode,'executable_here':d.cloud=='local','status':'local_execution' if d.cloud=='local' else 'export_plan_only','required_configuration':[] if d.cloud=='local' else ['Provisioned warehouse and approved network route','Workload identity with least-privilege staging/publish grants','External secret store if workload identity is unavailable','Object landing zone and cloud-specific bulk loader','Warehouse integration tests and rollback runbook'],'steps':['Retain raw bytes and extraction manifest in immutable Bronze','Validate schema, control totals and source-to-target mappings','Build typed Silver staging data, retain rejected rows','Run critical and warning checks before publication',load_behavior,'Validate loaded keys, counts and control totals','Rebuild approved Gold aggregates and record lineage'],'bulk_loading':{'sqlite':'Local executemany inside transaction','sqlserver':'ODBC bulk / bcp into staging','synapse':'COPY INTO from ADLS staging','snowflake':'COPY INTO from an approved external stage','databricks':'COPY INTO / governed Delta ingestion','redshift':'COPY from S3 with IAM role','bigquery':'BigQuery load job from GCS'}[d.warehouse],'limitations':[] if d.cloud=='local' else ['This plan does not connect to, provision or write a cloud warehouse.','CDC, streams, distributed scheduling and cloud bulk loaders require separate integration.']}


def ddl(spec):
    d=spec.destination;w=d.warehouse
    quote=(lambda name:'['+name+']') if w in ('sqlserver','synapse') else (lambda name:'`'+name+'`') if w in ('bigquery','databricks') else (lambda name:'"'+name+'"')
    def datatype(dtype):
        if dtype=='string':return 'STRING' if w in ('bigquery','databricks') else 'NVARCHAR(4000)' if w in ('sqlserver','synapse') else 'VARCHAR(65535)'
        if dtype=='integer':return 'INT64' if w=='bigquery' else 'BIGINT'
        return 'FLOAT64' if w=='bigquery' else 'FLOAT' if w in ('sqlserver','synapse') else 'DOUBLE PRECISION'
    namespace=d.namespace+'_silver';table=quote(namespace)+'.'+quote(spec.target)
    catalog=d.catalog
    if not catalog and w=='bigquery':catalog=d.settings.get('project_id')
    if not catalog and w in ('sqlserver','synapse','snowflake'):catalog=d.settings.get('database')
    if catalog:table=quote(catalog)+'.'+table
    columns=',\n  '.join(quote(c.name)+' '+datatype(c.dtype)+(' NOT NULL' if getattr(c,'required',False) else '') for c in [*spec.columns,*spec.derived_columns])
    return '-- Reference DDL only. Create the reviewed namespace first; verify physical design and constraints on the target.\nCREATE TABLE '+table+' (\n  '+columns+'\n);\n'


def artifacts(spec):
    return {'destination-plan.json':json.dumps(deployment_plan(spec),indent=2),'destination-ddl.sql':ddl(spec)}
