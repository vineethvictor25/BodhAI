"""Exact profiles for bounded extracts; contracts and auditable schema drift."""
import json
from datetime import datetime
from typing import Literal
from pydantic import Field
from . import store
from .mapping import flatten
from .models import StrictModel


class SourceContract(StrictModel):
    source_name:str=Field(min_length=1,max_length=200)
    owner:str=Field(min_length=1,max_length=200)
    update_pattern:Literal['unknown','append_only','mutable']='unknown'
    ingestion_pattern:Literal['full','watermark','cdc','stream']='full'
    expected_schema:dict[str,list[Literal['string','integer','number','boolean','object','array','null']]]=Field(default_factory=dict,max_length=200)
    column_order:list[str]=Field(default_factory=list,max_length=200)
    drift_policy:Literal['fail','alert','evolve']='fail'
    expected_rows:int|None=Field(default=None,ge=0)
    control_column:str|None=None
    control_total:float|None=None
    control_tolerance:float=Field(default=0,ge=0)
    classification:Literal['internal','public','confidential','restricted']='internal'
    pii_columns:list[str]=Field(default_factory=list,max_length=100)
    pii_key_ref:str=Field(default='PII_ENCRYPTION_KEY',pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,120}$')


def typename(v):
    if v is None:return 'null'
    if isinstance(v,bool):return 'boolean'
    if isinstance(v,int):return 'integer'
    if isinstance(v,float):return 'number'
    if isinstance(v,dict):return 'object'
    if isinstance(v,list):return 'array'
    return 'string'


def profile(rows):
    flat=[flatten(r) for r in rows];paths=list(dict.fromkeys(k for r in flat for k in r))
    result=[]
    for key in paths:
        values=[r.get(key) for r in flat];nulls=sum(v is None for v in values);types={}
        for v in values:types[typename(v)]=types.get(typename(v),0)+1
        distinct={json.dumps(v,sort_keys=True,allow_nan=False) for v in values if v is not None}
        numeric=[v for v in values if isinstance(v,(int,float)) and not isinstance(v,bool)]
        result.append({'column':key,'types':types,'null_count':nulls,'null_percent':round(100*nulls/len(rows),4),'cardinality':len(distinct),'minimum':min(numeric) if numeric else None,'maximum':max(numeric) if numeric else None})
    return {'row_count':len(rows),'columns':result,'schema':{c['column']:list(c['types']) for c in result},'column_order':paths,'scope':'Exact statistics over this bounded extract, not a scan of an upstream database.'}


def validate_source(name,rows):
    observed=profile(rows)
    with store.db() as conn:row=conn.execute('SELECT * FROM source_contracts WHERE source_name=?',(name,)).fetchone()
    if row is None:return {'profile':observed,'contract_revision':None,'drift':None,'reconciliation':'no control file/contract supplied'}
    contract=SourceContract.model_validate_json(row['contract_json']);expected=contract.expected_schema;actual=observed['schema']
    if contract.ingestion_pattern!='full':raise ValueError('This file/API runtime supports full snapshots; watermark, CDC and stream ingestion need a separately configured connector')
    drift={'added':sorted(set(actual)-set(expected)) if expected else [],'dropped':sorted(set(expected)-set(actual)), 'type_changes':{k:{'expected':expected[k],'actual':actual[k]} for k in expected.keys()&actual.keys() if not set(actual[k]).issubset(expected[k])},'order_changed':bool(contract.column_order and contract.column_order!=observed['column_order'])}
    changed=any(drift.values())
    if changed and contract.drift_policy=='fail':raise ValueError('Source schema drift blocked ingestion; inspect the source contract and profile')
    if contract.expected_rows is not None and contract.expected_rows!=len(rows):raise ValueError('Source-to-landing row-count reconciliation failed')
    total=None
    if contract.control_total is not None:
        import math
        try:total=math.fsum(float(flatten(r)[contract.control_column]) for r in rows)
        except (KeyError,TypeError,ValueError):raise ValueError('Source control-total column cannot be evaluated') from None
        if not math.isfinite(total) or abs(total-contract.control_total)>contract.control_tolerance:raise ValueError('Source-to-landing control-total reconciliation failed')
    return {'profile':observed,'contract_revision':row['revision'],'drift':drift,'drift_policy':contract.drift_policy,'schema_action':'accept_observed_extract' if changed and contract.drift_policy=='evolve' else 'alert' if changed else 'unchanged','reconciliation':{'expected_rows':contract.expected_rows,'actual_rows':len(rows),'control_total':total},'declarations':{'owner':contract.owner,'update_pattern':contract.update_pattern,'ingestion_pattern':contract.ingestion_pattern,'classification':contract.classification}}


def validate_source_summary(name,row_count,schema,control_total=None):
    with store.db() as conn:row=conn.execute('SELECT * FROM source_contracts WHERE source_name=?',(name,)).fetchone()
    if row is None:return {'contract_revision':None,'drift':None,'reconciliation':'no control file/contract supplied'}
    contract=SourceContract.model_validate_json(row['contract_json']);expected=contract.expected_schema
    if contract.ingestion_pattern!='full':raise ValueError('This file/API runtime supports full snapshots; watermark, CDC and stream ingestion need a separately configured connector')
    drift={'added':sorted(set(schema)-set(expected)) if expected else [],'dropped':sorted(set(expected)-set(schema)),'type_changes':{k:{'expected':expected[k],'actual':schema[k]} for k in expected.keys()&schema.keys() if not set(schema[k]).issubset(expected[k])},'order_changed':bool(contract.column_order and contract.column_order!=list(schema))}
    changed=any(drift.values())
    if changed and contract.drift_policy=='fail':raise ValueError('Source schema drift blocked ingestion; inspect the source contract and profile')
    if contract.expected_rows is not None and contract.expected_rows!=row_count:raise ValueError('Source-to-landing row-count reconciliation failed')
    total=None
    if contract.control_total is not None:
        if control_total is None:raise ValueError('Source control-total column cannot be evaluated')
        total=control_total
        if abs(total-contract.control_total)>contract.control_tolerance:raise ValueError('Source-to-landing control-total reconciliation failed')
    return {'contract_revision':row['revision'],'drift':drift,'drift_policy':contract.drift_policy,'schema_action':'accept_observed_extract' if changed and contract.drift_policy=='evolve' else 'alert' if changed else 'unchanged','reconciliation':{'expected_rows':contract.expected_rows,'actual_rows':row_count,'control_total':total},'declarations':{'owner':contract.owner,'update_pattern':contract.update_pattern,'ingestion_pattern':contract.ingestion_pattern,'classification':contract.classification}}


def dataset_profile(dataset_id):
    data=store.get_dataset_info(dataset_id);rows=store.get_dataset_sample(dataset_id);result=profile(rows)
    if data['row_count']>len(rows):
        result['row_count']=data['row_count']
        result['scope']=f"Column statistics sampled from the first {len(rows):,} rows of {data['row_count']:,}; row count is exact."
    from .privacy import sensitive_source_paths
    protected=sensitive_source_paths(data['metadata'])
    for column in result['columns']:
        if column['column'] in protected:column.update({'protected':True,'cardinality':None,'minimum':None,'maximum':None,'note':'Value statistics withheld for sensitive columns'})
    with store.db() as conn:
        previous=conn.execute('SELECT id,rows_json,metadata_json,created_at FROM datasets WHERE name=? AND created_at<? ORDER BY created_at DESC LIMIT 1',(data['name'],data['created_at'])).fetchone()
    trend=None
    if previous:
        seconds=(datetime.fromisoformat(data['created_at'])-datetime.fromisoformat(previous['created_at'])).total_seconds()
        old_count=json.loads(previous['metadata_json']).get('row_count',len(json.loads(previous['rows_json'])))
        delta=data['row_count']-old_count
        trend={'previous_dataset_id':previous['id'],'previous_rows':old_count,'row_delta':delta,'rows_per_day':delta*86400/seconds if seconds>0 else None,'note':'Difference between same-named uploaded snapshots; not a measured source-system growth rate.'}
    return {'dataset_id':dataset_id,'name':data['name'],**result,'snapshot_trend':trend,'source_contract':data['metadata'].get('extraction',{}),'inferred_update_pattern':'unknown; declare append-only or mutable in the source contract'}
