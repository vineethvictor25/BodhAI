import hashlib
import io
import json
from pathlib import Path
import pytest
import pyarrow as pa
import pyarrow.parquet as pq
from fastapi.testclient import TestClient
from app import store
from app.main import app
from app.ingest import ingest_bytes,decode_rows
from app.engine import execute
from app.models import PipelineSpec,RunRequest
from app.profiling import profile


@pytest.fixture
def client():
    with TestClient(app) as c:yield c


def spec(**updates):
    data={'name':'test_etl','target':'items','primary_key':'id','max_reject_ratio':0,
          'columns':[{'name':'id','required':True},{'name':'amount','dtype':'number','required':True},{'name':'category'},{'name':'updated_at','transforms':['iso_timestamp']}]}
    data.update(updates);return PipelineSpec.model_validate(data)


def dataset(rows,name='source.json'):
    return ingest_bytes(json.dumps(rows).encode(),name)['id']


def row(key='1',amount=10,category='a',stamp='2026-11-01T12:00:00Z'):
    return {'id':key,'amount':amount,'category':category,'updated_at':stamp}


def run(s,rows,key='batch'):
    return execute(RunRequest(spec=s,dataset_ids=[dataset(rows)],idempotency_key=key))


def current(target='items'):
    with store.db() as c:return [json.loads(r[0]) for r in c.execute('SELECT record_json FROM warehouse WHERE target=? ORDER BY record_key',(target,))]


@pytest.mark.parametrize('name,raw,expected,delimiter',[
 ('file.csv',b'id,amount\n1,10\n',[{'id':'1','amount':'10'}],None),
 ('file.tsv',b'id\tamount\n1\t10\n',[{'id':'1','amount':'10'}],None),
 ('file.json',b'{"data":[{"id":"1","amount":10}]}',[{'id':'1','amount':10}],None),
 ('file.jsonl',b'{"id":"1"}\n{"id":"2"}\n',[{'id':'1'},{'id':'2'}],None),
 ('file.txt',b'hello\nworld\n',[{'record_id':'1','text':'hello'},{'record_id':'2','text':'world'}],None),
 ('file.txt',b'id|amount\n1|10\n',[{'id':'1','amount':'10'}],'|')])
def test_formats_capture_exact_bronze(client,name,raw,expected,delimiter):
    response=client.post('/api/datasets/upload',files={'file':(name,raw)},data={'delimiter':delimiter} if delimiter else {})
    assert response.status_code==200,response.text
    d=response.json();assert store.get_dataset(d['id'])['rows']==expected
    assert client.get(f"/api/datasets/{d['id']}/bronze/raw").content==raw
    assert d['sha256']==hashlib.sha256(raw).hexdigest()
    assert d['metadata']['_file_name']==name


def test_real_parquet_roundtrip_and_limits(client,monkeypatch):
    sink=pa.BufferOutputStream();pq.write_table(pa.Table.from_pylist([row()]),sink)
    raw=sink.getvalue().to_pybytes();assert decode_rows(raw,'x.parquet')[0]['amount']==10
    r=run(spec(),[row()]);export=client.get(f"/api/runs/{r['id']}/export?layer=silver&format=parquet")
    assert export.status_code==200
    assert pq.read_table(pa.BufferReader(export.content)).to_pylist()[0]['id']=='1'
    monkeypatch.setenv('MAX_PARQUET_EXPANDED_BYTES','1')
    with pytest.raises(ValueError,match='expanded'):decode_rows(raw,'x.parquet')


def test_malformed_source_not_retained_and_audited(client):
    raw=b'id,amount\n1,2,3\n';response=client.post('/api/datasets/upload',files={'file':('bad.csv',raw)})
    assert response.status_code==400
    batch=client.get('/api/extractions').json()[0]
    assert batch['status']=='rejected' and batch['dataset_id'] is None
    assert client.get(f"/api/extractions/{batch['id']}/raw").status_code==400
    assert batch['details']['raw_retained'] is False
    assert client.get('/api/datasets').json()==[]


def test_profile_statistics():
    result=profile([{'id':1,'a':None},{'id':2,'a':'x'},{'id':2}]);columns={c['column']:c for c in result['columns']}
    assert columns['a']['null_count']==2 and columns['a']['cardinality']==1
    assert columns['id']['cardinality']==2 and columns['id']['minimum']==1


def test_source_schema_and_control_total_contract(client):
    contract={'source_name':'control.csv','owner':'team','expected_schema':{'id':['string'],'amount':['string']},'expected_rows':1,'control_column':'amount','control_total':10}
    assert client.post('/api/sources/contracts',json=contract).json()['revision']==1
    assert client.post('/api/datasets/upload',files={'file':('control.csv',b'id,amount\n1,10\n')}).status_code==200
    assert client.post('/api/datasets/upload',files={'file':('control.csv',b'id,amount,new\n1,10,x\n')}).status_code==400
    assert client.post('/api/datasets/upload',files={'file':('control.csv',b'id,amount\n1,11\n')}).status_code==400
    contract.update(drift_policy='alert',control_total=11)
    assert client.post('/api/sources/contracts',json=contract).json()['revision']==2
    result=client.post('/api/datasets/upload',files={'file':('control.csv',b'id,amount,new\n1,11,x\n')}).json()
    assert result['metadata']['extraction']['schema_action']=='alert'


def test_multi_source_sample_pipeline_and_replay(client):
    sample=client.post('/api/medallion/sample',json={});assert sample.status_code==200,sample.text
    data=sample.json();body={'spec':data['spec'],'dataset_ids':[d['id'] for d in data['datasets']],'idempotency_key':'orders-batch'}
    preview=client.post('/api/medallion/preview',json=body).json()
    assert (preview['total'],preview['accepted'],preview['rejected'],preview['filtered'])==(8,5,1,2)
    assert client.get('/api/runs').json()==[]
    response=client.post('/api/runs',json=body);assert response.status_code==200,response.text
    r=response.json();assert r['status']=='succeeded'
    assert sum(g['revenue'] for g in r['report']['gold_preview'])==710
    assert r['report']['post_load_reconciled'] is True
    assert client.post('/api/runs',json=body).json()['id']==r['id']
    assert len(client.get(f"/api/runs/{r['id']}/filtered").json())==2
    assert len(client.get(f"/api/runs/{r['id']}/sttm").json()['source_mappings'])==4


def test_streamed_api_run_reports_progress_and_rejects_cross_chunk_duplicates(client,monkeypatch):
    import time
    import app.engine as engine
    original_iterator=store.iter_dataset_rows
    monkeypatch.setattr(engine,'MAX_IN_MEMORY_ROWS',1)
    monkeypatch.setattr(store,'iter_dataset_rows',lambda dataset_id,batch_size=25000:original_iterator(dataset_id,batch_size=1))
    data=ingest_bytes(b'id,amount,category,updated_at\n1,10,a,2026-11-01T12:00:00Z\n1,20,b,2026-11-02T12:00:00Z\n','duplicate.csv')
    body={'spec':spec(max_reject_ratio=0.5).model_dump(),'dataset_ids':[data['id']],'idempotency_key':'streamed-duplicate'}

    response=client.post('/api/runs',json=body)
    assert response.status_code==200,response.text
    run=response.json()
    deadline=time.monotonic()+10
    while run['status']=='running' and time.monotonic()<deadline:
        time.sleep(0.01)
        run=client.get(f"/api/runs/{run['id']}").json()

    assert run['status']=='succeeded'
    assert run['report']['progress']=={'phase':'complete','processed_rows':2,'total_rows':2,'percent':100}
    assert (run['report']['accepted'],run['report']['rejected'],run['report']['published'])==(1,1,1)
    assert len(current())==1


def test_streamed_critical_check_failure_preserves_existing_target(monkeypatch):
    import app.engine as engine
    monkeypatch.setattr(engine,'MAX_IN_MEMORY_ROWS',0)
    initial=run(spec(),[row('existing')],'initial-stream-target')
    assert initial['status']=='succeeded'
    before=current()
    incoming=ingest_bytes(b'id,amount,category,updated_at\nnew,10,a,2026-11-01T12:00:00Z\n','quality.csv')
    checked=spec(batch_checks=[{'name':'minimum_total','kind':'sum','column':'amount','minimum':100}])
    result=execute(RunRequest(spec=checked,dataset_ids=[incoming['id']],idempotency_key='failed-stream-check'))
    assert result['status']=='quality_failed'
    assert result['report']['published']==0
    assert current()==before


def test_union_and_safe_joins(client):
    a=dataset([{'id':'1','customer':'C1'},{'id':'2','customer':'missing'}]);b=dataset([{'customer':'C1','name':'Ada'}])
    joined=client.post('/api/datasets/merge',json={'dataset_ids':[a,b],'mode':'left_join','left_key':'customer','right_key':'customer'})
    assert joined.status_code==200,joined.text
    rows=store.get_dataset(joined.json()['id'])['rows'];assert len(rows)==2 and rows[1]['right']=={}
    assert len(joined.json()['metadata']['parents'])==2
    assert client.post('/api/datasets/merge',json={'dataset_ids':[a,b]}).status_code==400
    b2=dataset([{'customer':'C1','name':'Ada'},{'customer':'C1','name':'Other'}])
    assert client.post('/api/datasets/merge',json={'dataset_ids':[a,b2],'mode':'left_join','left_key':'customer','right_key':'customer'}).status_code==400
    a2=dataset([{'id':'3','customer':'C2'}]);merged=client.post('/api/datasets/merge',json={'dataset_ids':[a,a2]}).json()
    assert merged['row_count']==3


def test_failed_quality_gate_keeps_current_gold(client):
    s=spec(gold={'group_by':['category'],'measures':[{'name':'total','operation':'sum','column':'amount'}]})
    first=run(s,[row()]);s.batch_checks=[]
    s2=spec(gold=s.gold.model_dump(),batch_checks=[{'name':'min_rows','kind':'row_count','minimum':2}])
    second=run(s2,[row(amount=99)],'failed')
    assert second['status']=='quality_failed' and current()[0]['amount']==10
    assert client.get('/api/medallion/gold?target=items').json()['run_id']==first['id']
    assert client.get(f"/api/runs/{second['id']}/export").status_code==409
    assert client.get(f"/api/runs/{second['id']}/layers/silver").json()['published'] is False


def test_gold_rebuilds_full_current_detail(client):
    s=spec(gold={'group_by':['category'],'measures':[{'name':'total','operation':'sum','column':'amount'}]})
    run(s,[row()]);second=run(s,[row('2',20)],'next')
    assert second['report']['gold_preview']==[{'category':'a','total':30.0}]
    assert len(client.get('/api/medallion/gold?target=items').json()['contributing_runs'])==2


@pytest.mark.parametrize('mode,expected',[('upsert',2),('full_refresh',1),('append',2)])
def test_load_strategies(mode,expected):
    s=spec(load={'mode':mode});run(s,[row()]);assert run(s,[row('2')],'next')['status']=='succeeded';assert len(current())==expected


def test_upsert_skips_existing_keys_by_default_and_can_explicitly_update():
    first=run(spec(),[row(amount=10)])
    skipped=run(spec(),[row(amount=99)],'skip-existing')
    assert first['status']==skipped['status']=='succeeded'
    assert skipped['report']['accepted']==1
    assert skipped['report']['published']==0
    assert skipped['report']['skipped_existing']==1
    assert current()[0]['amount']==10

    update_spec=spec(load={'mode':'upsert','duplicate_policy':'update'})
    updated=run(update_spec,[row(amount=99)],'update-existing')
    assert updated['status']=='succeeded'
    assert updated['report']['published']==1
    assert updated['report']['skipped_existing']==0
    assert current()[0]['amount']==99


def test_append_conflict_blocks_atomically():
    s=spec(load={'mode':'append'});run(s,[row()]);r=run(s,[row(amount=99)],'again')
    assert r['status']=='load_blocked' and current()[0]['amount']==10


def test_partition_overwrite_preserves_other_partitions():
    run(spec(),[row('1',category='a'),row('2',category='b')])
    s=spec(load={'mode':'partition_overwrite','partition_column':'category','partition_values':['a']})
    assert run(s,[row('3',category='a')],'partition')['status']=='succeeded'
    assert {r['id'] for r in current()}=={'2','3'}
    assert run(s,[row('2',category='a')],'collision')['status']=='load_blocked'
    assert {r['id'] for r in current()}=={'2','3'}


def test_scd2_history_idempotent_unchanged_and_changed(client):
    s=spec(load={'mode':'scd2','tracked_columns':['amount','category']})
    run(s,[row()]);run(s,[row()],'unchanged');run(s,[row(amount=20)],'changed')
    history=client.get('/api/dimensions/history?target=items').json()
    assert len(history)==2 and [h['is_current'] for h in history]==[0,1]
    assert history[0]['effective_to']==history[1]['effective_from']
    assert history[0]['surrogate_key']!=history[1]['surrogate_key']


def test_critical_and_warning_checks():
    warning=spec(batch_checks=[{'name':'volume','kind':'row_count','minimum':3,'severity':'warning'}])
    r=run(warning,[row()]);assert r['status']=='succeeded' and r['report']['quality_checks'][0]['passed'] is False
    critical=spec(batch_checks=[{'name':'control','kind':'sum','column':'amount','minimum':50}])
    assert run(critical,[row('2',1)],'critical')['status']=='quality_failed'


def test_derived_division_error_is_quarantined():
    s=spec(derived_columns=[{'name':'ratio','operation':'divide','fields':['amount','amount']}])
    r=run(s,[row(amount=0)]);assert r['status']=='quality_failed' and r['report']['rejected']==1


def test_atomic_rollback_on_gold_failure(monkeypatch):
    s=spec(gold={'group_by':[],'measures':[{'name':'total','operation':'sum','column':'amount'}]})
    run(s,[row()])
    from app import engine
    def broken(*args):raise RuntimeError('simulated gold failure')
    monkeypatch.setattr(engine,'publish_gold',broken)
    with pytest.raises(RuntimeError):run(s,[row('2',99)],'crash')
    assert current()==[{'id':'1','amount':10.0,'category':'a','updated_at':'2026-11-01T12:00:00+00:00'}]


def test_destination_catalog_validation_and_no_silent_local_write(client):
    catalog=client.get('/api/destinations/catalog').json();assert set(catalog)=={'local','azure','aws','gcp'}
    for cloud,entry in catalog.items():
        for warehouse in entry['warehouses']:
            s=spec(destination={'cloud':cloud,'warehouse':warehouse['id']})
            artifacts=client.post('/api/artifacts',json=s.model_dump());assert artifacts.status_code==200,artifacts.text
            plan=json.loads(artifacts.json()['destination-plan.json'])
            assert plan['executable_here']==(cloud=='local')
    bad=spec().model_dump();bad['destination']={'cloud':'azure','warehouse':'bigquery'}
    assert client.post('/api/artifacts',json=bad).status_code==422
    cloud=spec(destination={'cloud':'gcp','warehouse':'bigquery'})
    body={'spec':cloud.model_dump(),'dataset_ids':[dataset([row()])],'idempotency_key':'cloud'}
    assert client.post('/api/medallion/preview',json=body).status_code==200
    assert client.post('/api/runs',json=body).status_code==400
    assert client.get('/api/runs').json()==[]


def test_destination_specific_settings_validation(client):
    s=spec(destination={'cloud':'aws','warehouse':'redshift','settings':{'storage_uri':'s3://bucket/prefix','identity_ref':'workload-identity://loader','database':'analytics','endpoint':'cluster.example.com','iam_role_arn':'arn:aws:iam::123456789012:role/Loader'}})
    result=client.post('/api/artifacts',json=s.model_dump()).json();assert json.loads(result['destination-plan.json'])['configuration_complete'] is True
    for settings in ({'password':'secret'},{'storage_uri':'gs://wrong-cloud/prefix'},{'identity_ref':'actual-secret-key'}):
        bad=s.model_dump();bad['destination']['settings']=settings
        assert client.post('/api/artifacts',json=bad).status_code==422
    azure=spec(destination={'cloud':'azure','warehouse':'sqlserver','settings':{'database':'Commerce','staging_schema':'load_work'}})
    artifacts=client.post('/api/artifacts',json=azure.model_dump()).json()
    assert '[Commerce].[analytics_silver].[items]' in artifacts['destination-ddl.sql']
    assert json.loads(artifacts['destination-plan.json'])['staging_table']=='load_work.items_stage'
