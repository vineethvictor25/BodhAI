"""File-oriented ETL APIs: explicit merges, preview, layer inspection and exports."""
import csv
import io
import json
from typing import Literal
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import Response
from pydantic import Field
from . import store
from .engine import transform_batch, ConflictError
from .merge_engine import merge, preview_merge
from .mapping import flatten
from .models import StrictModel, RunRequest
from .profiling import SourceContract,dataset_profile


class MergeRequest(StrictModel):
    name:str=Field(default='merged_source',min_length=1,max_length=120)
    dataset_ids:list[str]=Field(min_length=2,max_length=20)
    mode:Literal['append','inner_join','left_join']='append'
    left_key:str=Field(default='',max_length=300)
    right_key:str=Field(default='',max_length=300)


class DecryptRequest(StrictModel):
    run_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    column:str=Field(pattern=r'^[a-z][a-z0-9_]{0,62}$')
    row_numbers:list[int]=Field(min_length=1,max_length=20)


def layer_rows(run_id,layer):
    run=store.get_run(run_id)
    with store.db() as conn:
        if layer=='silver':
            data=conn.execute('SELECT record_json FROM silver WHERE run_id=? ORDER BY row_number',(run_id,)).fetchall()
            rows=[json.loads(r[0]) for r in data]
        else:
            data=conn.execute('SELECT rows_json FROM gold_snapshots WHERE run_id=?',(run_id,)).fetchone()
            rows=json.loads(data[0]) if data else []
    return run,rows


def router(authorize):
    api=APIRouter(prefix='/api',dependencies=[Depends(authorize)])

    @api.get('/privacy/status')
    def privacy_status():
        import os
        return {'column_encryption':'AES-256-GCM, random nonce and column-name authenticated context','key_provider':os.getenv('PII_KEY_PROVIDER','environment'),'key_configuration_present':bool(os.getenv('PII_ENCRYPTION_KEY') or os.getenv('PII_KEY_VAULT_URL')),'decrypt_enabled':len(os.getenv('PII_DECRYPT_TOKEN',''))>=32,'raw_policy':'Sensitive columns are encrypted in the replay snapshot; original PII file bytes are not retained','detection':'Heuristics plus declared pii_columns; manual classification is required','existing_data':'Existing datasets and backups are not retroactively protected. Review and migrate before onboarding real PII.'}

    @api.post('/privacy/decrypt')
    def decrypt_column(body:DecryptRequest,request:Request):
        import os,hmac
        expected=os.getenv('PII_DECRYPT_TOKEN','');supplied=request.headers.get('X-PII-Access-Token','')
        if len(expected)<32 or not hmac.compare_digest(expected.encode(),supplied.encode()):raise HTTPException(403,'Separately authorized PII access is required; decryption is disabled by default')
        run,rows=layer_rows(body.run_id,'silver')
        column=next((c for c in run['request']['spec']['columns'] if c['name']==body.column),None)
        if not column or column.get('protection')!='encrypt':raise ValueError('Select an encrypted column from this run')
        if any(n<1 for n in body.row_numbers):raise ValueError('Row numbers must be positive')
        from .privacy import decrypt
        result=[{'row_number':r['row_number'],'value':decrypt(r['record'][body.column],body.column)} for r in rows if r['row_number'] in body.row_numbers]
        with store.db() as conn:store.audit(conn,body.run_id,'privacy.column_decrypted',{'column':body.column,'row_count':len(result)})
        return {'column':body.column,'rows':result}

    @api.get('/destinations/catalog')
    def destinations():
        from .destinations import public_catalog
        return public_catalog()

    @api.post('/destinations/plan')
    def destination_plan(body:RunRequest):
        from .destinations import deployment_plan,ddl
        return {'plan':deployment_plan(body.spec),'ddl':ddl(body.spec)}

    @api.post('/datasets/merge')
    def merge_sources(body:MergeRequest):return merge(body)

    @api.post('/datasets/merge/preview')
    def merge_preview(body:MergeRequest):return preview_merge(body)

    @api.get('/datasets/{dataset_id}/profile')
    def source_profile(dataset_id:str):return dataset_profile(dataset_id)

    @api.get('/datasets/{dataset_id}/suggested-spec')
    def suggested_spec(dataset_id:str):
        from .spec_inference import suggest_spec
        return suggest_spec(dataset_id)

    @api.post('/sources/contracts')
    def source_contract(body:SourceContract):
        with store.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT revision FROM source_contracts WHERE source_name=?',(body.source_name,)).fetchone()
            revision=row[0]+1 if row else 1;stamp=store.now()
            conn.execute('INSERT INTO source_contract_versions VALUES(?,?,?,?)',(body.source_name,revision,body.model_dump_json(),stamp))
            conn.execute('INSERT INTO source_contracts VALUES(?,?,?,?) ON CONFLICT(source_name) DO UPDATE SET revision=excluded.revision,contract_json=excluded.contract_json,updated_at=excluded.updated_at',(body.source_name,revision,body.model_dump_json(),stamp))
            store.audit(conn,None,'source.contract_saved',{'source_name':body.source_name,'revision':revision})
        return {'revision':revision,'contract':body.model_dump()}

    @api.get('/sources/contracts')
    def source_contracts():
        with store.db() as conn:rows=conn.execute('SELECT * FROM source_contract_versions ORDER BY source_name,revision').fetchall()
        return [{'source_name':r['source_name'],'revision':r['revision'],'contract':json.loads(r['contract_json'])} for r in rows]

    @api.get('/extractions')
    def extraction_audit():
        with store.db() as conn:rows=conn.execute('SELECT id,source_name,started_at,finished_at,status,sha256,dataset_id,details_json FROM extraction_batches ORDER BY started_at DESC LIMIT 100').fetchall()
        return [{**{k:r[k] for k in r.keys() if k!='details_json'},'details':json.loads(r['details_json'])} for r in rows]

    @api.get('/extractions/{batch_id}')
    def extraction_status(batch_id:str):
        with store.db() as conn:r=conn.execute('SELECT id,source_name,started_at,finished_at,status,dataset_id,details_json FROM extraction_batches WHERE id=?',(batch_id,)).fetchone()
        if r is None:raise HTTPException(404,'Upload progress was not found')
        return {**{k:r[k] for k in r.keys() if k!='details_json'},'details':json.loads(r['details_json'])}

    @api.get('/extractions/{batch_id}/raw')
    def rejected_extract(batch_id:str):
        with store.db() as conn:r=conn.execute('SELECT content FROM extraction_batches WHERE id=?',(batch_id,)).fetchone()
        if r is None:raise ValueError('Extraction batch not found')
        if not r[0]:raise ValueError('Unparsed source bytes were not retained; fix the source and upload again')
        return Response(bytes(r[0]),media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="landing-source.bin"'})

    @api.get('/dimensions/history')
    def dimension_history(target:str,limit:int=100,offset:int=0):
        if offset<0 or not 1<=limit<=1000:raise ValueError('Use offset >= 0 and limit 1–1000')
        with store.db() as conn:rows=conn.execute('SELECT * FROM dimension_history WHERE target=? ORDER BY business_key,version LIMIT ? OFFSET ?',(target,limit,offset)).fetchall()
        return [{**{k:r[k] for k in r.keys() if k!='record_json'},'record':json.loads(r['record_json'])} for r in rows]

    @api.get('/runs/{run_id}/sttm')
    def sttm(run_id:str):
        run=store.get_run(run_id);spec=run['request']['spec']
        return {'run_id':run_id,'business':spec.get('business'),'target':spec['target'],'primary_key':spec['primary_key'],'source_mappings':run['report'].get('mappings',[]),'columns':spec['columns'],'derived_columns':spec.get('derived_columns',[]),'row_filters':spec.get('row_filters',[]),'gold':spec.get('gold'),'load':spec.get('load',{'mode':'upsert'}),'quality_checks':spec.get('batch_checks',[]),'version_ref':run['report'].get('pipeline_version'),'contract':'Executable specification and resolved per-source mappings retained with the run.'}

    @api.get('/runs/{run_id}/filtered')
    def filtered(run_id:str):
        store.get_run(run_id)
        with store.db() as conn:rows=conn.execute('SELECT record_json FROM filtered_rows WHERE run_id=? ORDER BY row_number',(run_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    @api.post('/medallion/preview')
    def preview(body:RunRequest):
        if sum(store.get_dataset_info(dataset_id)['row_count'] for dataset_id in body.dataset_ids)>25_000:
            raise ValueError('Preview is limited to 25,000 total rows; streamed pipeline runs remain available for larger sources')
        datasets=[store.get_dataset(i) for i in body.dataset_ids]
        approved=None
        if body.approved_mapping_id:
            from .semantic_mapping import resolve_approved
            approved=resolve_approved(body.approved_mapping_id,body.spec,datasets)
        if body.pipeline_id:
            from .pipeline_changes import execution_mapping
            approved=execution_mapping(body,datasets)
        batch=transform_batch(body.spec,datasets,approved)
        with store.db() as conn:
            existing=conn.execute('SELECT contract_json FROM target_contracts WHERE target=?',(body.spec.target,)).fetchone()
            from .loading import checks,preflight
            quality=checks(conn,body.spec,batch['accepted']);load_error=preflight(conn,body.spec,batch['accepted'])
        return {'status':batch['status'],'total':batch['total'],'accepted':len(batch['accepted']),'rejected':len(batch['rejected']),'filtered':len(batch['filtered']),'reject_ratio':batch['ratio'],'silver_preview':batch['accepted'][:100],'gold_preview':batch['gold_preview'][:100],'issues':batch['rejected'][:100],'quality_checks':quality,'load_error':load_error,'contract_error':bool(existing and existing[0]!=batch['contract']),'scope':'No writes. Gold preview covers this input batch only; execution rebuilds Gold from the full published detail target.'}

    @api.post('/medallion/sample')
    def sample():
        from pathlib import Path
        from .ingest import ingest_bytes
        from .models import PipelineSpec
        root=Path(__file__).resolve().parent.parent/'samples'/'medallion'
        spec=PipelineSpec.model_validate_json((root/'orders_spec.json').read_text())
        sources=[ingest_bytes((root/name).read_bytes(),name,'|' if name.endswith('.txt') else None) for name in ['orders.csv','orders.json','orders.parquet','orders.txt']]
        return {'spec':spec.model_dump(),'datasets':sources,'note':'Sources staged only. Preview, then explicitly run the reviewed specification.'}

    @api.get('/datasets/{dataset_id}/bronze')
    def bronze(dataset_id:str):
        data=store.get_dataset_info(dataset_id)
        with store.db() as conn:obj=conn.execute('SELECT representation,byte_count FROM bronze_objects WHERE dataset_id=?',(dataset_id,)).fetchone()
        preview=[]
        if data['row_count']:
            _,preview=next(store.iter_dataset_rows(dataset_id,batch_size=20))
        return {'dataset_id':dataset_id,'sha256':data['sha256'],'name':data['name'],'rows':data['row_count'],'metadata':data['metadata'],'original_available':bool(obj and obj['representation']=='original_bytes'),'stored_object':dict(obj) if obj else None,'preview':preview[:20]}

    @api.get('/datasets/{dataset_id}/bronze/raw')
    def raw_bronze(dataset_id:str):
        store.get_dataset_info(dataset_id)
        with store.db() as conn:obj=conn.execute('SELECT content FROM bronze_objects WHERE dataset_id=?',(dataset_id,)).fetchone()
        if obj is None:raise ValueError('Original bytes were not retained for this older dataset; upload again to capture Bronze')
        return Response(bytes(obj[0]),media_type='application/octet-stream',headers={'Content-Disposition':'attachment; filename="bronze-source.bin"'})

    @api.get('/runs/{run_id}/layers/{layer}')
    def layers(run_id:str,layer:Literal['silver','gold'],offset:int=0,limit:int=100):
        if offset<0 or not 1<=limit<=1000:raise ValueError('Use offset >= 0 and limit 1–1000')
        run,rows=layer_rows(run_id,layer)
        return {'run_id':run_id,'layer':layer,'run_status':run['status'],'published':run['status']=='succeeded' and (layer=='silver' or run['request']['spec'].get('gold') is not None),'total':len(rows),'rows':rows[offset:offset+limit]}

    @api.get('/runs/{run_id}/export')
    def export(run_id:str,layer:Literal['silver','gold']='silver',format:Literal['json','csv','parquet']='json'):
        run,rows=layer_rows(run_id,layer)
        # Failed-run Silver candidates remain inspectable but are never exported as approved data.
        if run['status']!='succeeded':raise ConflictError('Export requires a successful quality-gated run')
        records=[r['record'] for r in rows] if layer=='silver' else rows
        if not records:raise ValueError('This layer has no records')
        if format=='json':payload=json.dumps(records,allow_nan=False).encode();mime='application/json'
        elif format=='csv':
            stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
            payload=stream.getvalue().encode();mime='text/csv'
        else:
            import pyarrow as pa
            import pyarrow.parquet as pq
            sink=pa.BufferOutputStream();pq.write_table(pa.Table.from_pylist(records),sink,compression='snappy');payload=sink.getvalue().to_pybytes();mime='application/octet-stream'
        return Response(payload,media_type=mime,headers={'Content-Disposition':f'attachment; filename="{layer}-{run_id}.{format}"'})

    @api.get('/medallion/gold')
    def current_gold(target:str='products'):
        with store.db() as conn:
            row=conn.execute('SELECT s.* FROM gold_current c JOIN gold_snapshots s ON s.run_id=c.run_id WHERE c.target=?',(target,)).fetchone()
        return {'target':target,'run_id':row['run_id'] if row else None,'rows':json.loads(row['rows_json']) if row else [],'contributing_runs':json.loads(row['lineage_json']) if row else []}

    return api
