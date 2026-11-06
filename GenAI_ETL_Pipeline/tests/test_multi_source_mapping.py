import json
import pytest
from fastapi.testclient import TestClient
from app import semantic_mapping as sm,store
from app.ingest import save_rows
from app.main import app
from app.models import PipelineSpec,RunRequest
from app.engine import execute
from app.medallion_api import MergeRequest,preview_merge,merge


def spec():return PipelineSpec.model_validate({'primary_key':'id','target':'combined_test','max_reject_ratio':0,'columns':[{'name':'id','required':True},{'name':'category','required':True}]})


def entry(dataset_id,bindings):
    return sm.SourceMappingResponse(dataset_id=dataset_id,mappings=[sm.SemanticMatch(target=t,source=s,confidence=.99,reason='Synthetic fixture',evidence=[]) for t,s in bindings.items()],warnings=[])


def request(ids,mode='auto'):return sm.MappingRequest(spec=spec(),dataset_ids=ids,instructions='Combine these sources into the reviewed target. Retain unmatched base rows.',combination_mode=mode)


def decisions(proposal):return {d['dataset_id']:{m['target']:m['source'] for m in d['mappings']} for d in proposal['datasets']}


def assert_no_outputs():
    with store.db() as conn:
        assert conn.execute('SELECT count(*) FROM warehouse').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0]==0


def test_joint_append_uses_all_source_context_once_and_executes(monkeypatch):
    monkeypatch.setenv('PROVIDER','groq')
    a=save_rows([{'sku':'A','department':'home'}],'a.csv');b=save_rows([{'code':'B','group':'tech'}],'b.json')
    calls=[]
    def fake(task,payload,schema):
        calls.append(json.loads(payload));assert schema is sm.MultiSourceResponse
        assert {s['dataset_id'] for s in calls[0]['sources']}=={a['id'],b['id']}
        return sm.MultiSourceResponse(datasets=[entry(a['id'],{'id':'sku','category':'department'}),entry(b['id'],{'id':'code','category':'group'})],combination=sm.CombinationPlan(strategy='append',confidence=.99,reason='Same grain with different headers')),{'provider':'mock'}
    monkeypatch.setattr(sm,'structured',fake)
    proposal=sm.propose_mapping(request([a['id'],b['id']],'append'))
    assert len(calls)==1 and proposal['source_analysis']=='joint'
    assert proposal['combination']['preview']['input_rows']==2
    sm.approve_mapping(proposal['id'],sm.MappingApproval(approved=True,decisions=decisions(proposal)))
    r=execute(RunRequest(spec=spec(),dataset_ids=[a['id'],b['id']],approved_mapping_id=proposal['id'],idempotency_key='append'))
    assert r['status']=='succeeded' and r['report']['published']==2


@pytest.fixture
def joined(monkeypatch):
    monkeypatch.setenv('PROVIDER','groq')
    a=save_rows([{'id':'A','ref':'R1'},{'id':'B','ref':'missing'}],'base.csv')
    b=save_rows([{'ref':'R1','category':'home'}],'lookup.json')
    def response(confidence=.99,right_key='ref',strategy='left_join'):
        return sm.MultiSourceResponse(datasets=[entry(a['id'],{'id':'id','category':None}),entry(b['id'],{'id':None,'category':'category'})],combination=sm.CombinationPlan(strategy=strategy,left_dataset_id=a['id'],right_dataset_id=b['id'],left_key='ref',right_key=right_key,confidence=confidence,reason='Enrich base rows with lookup category'))
    monkeypatch.setattr(sm,'structured',lambda *args:(response(),{'provider':'mock'}))
    return a,b,response


def test_join_proposal_checks_row_impact_without_writes(joined):
    a,b,_=joined
    with store.db() as conn:before=conn.execute('SELECT count(*) FROM datasets').fetchone()[0]
    proposal=sm.propose_mapping(request([a['id'],b['id']]))
    plan=proposal['combination'];assert plan['eligible'] is True
    assert plan['preview']['output_rows']==2 and plan['preview']['unmatched_left_rows']==1
    assert plan['preview']['dropped_left_rows']==0
    with store.db() as conn:assert conn.execute('SELECT count(*) FROM datasets').fetchone()[0]==before
    assert_no_outputs()
    with pytest.raises(ValueError,match='recommends a join'):sm.approve_mapping(proposal['id'],sm.MappingApproval(approved=True,decisions=decisions(proposal)))


def test_join_create_then_final_mapping_and_run(joined,monkeypatch):
    a,b,_=joined
    body=MergeRequest(dataset_ids=[a['id'],b['id']],mode='left_join',left_key='ref',right_key='ref')
    assert preview_merge(body)['output_rows']==2
    combined=merge(body);assert_no_outputs()
    final_spec=spec();final_spec.columns[1].default='Unknown'
    monkeypatch.setattr(sm,'structured',lambda *args:(sm.SemanticResponse(mappings=[sm.SemanticMatch(target=t,source=s,confidence=.99,reason='Combined field',evidence=[]) for t,s in {'id':'left.id','category':'right.category'}.items()],warnings=[]),{'provider':'mock'}))
    proposal=sm.propose_mapping(sm.MappingRequest(spec=final_spec,dataset_ids=[combined['id']],instructions='Map the combined source columns'))
    sm.approve_mapping(proposal['id'],sm.MappingApproval(approved=True,decisions=decisions(proposal)))
    r=execute(RunRequest(spec=final_spec,dataset_ids=[combined['id']],approved_mapping_id=proposal['id'],idempotency_key='joined'))
    assert r['status']=='succeeded' and r['report']['published']==2
    assert r['report']['preview'][1]['record']['category']=='Unknown'


def test_invented_join_path_rejected(joined,monkeypatch):
    a,b,response=joined
    monkeypatch.setattr(sm,'structured',lambda *args:(response(right_key='invented'),{}))
    with pytest.raises(ValueError,match='does not exist'):sm.propose_mapping(request([a['id'],b['id']]))
    assert_no_outputs()


def test_low_confidence_join_is_not_executable(joined,monkeypatch):
    a,b,response=joined
    monkeypatch.setattr(sm,'structured',lambda *args:(response(confidence=.2),{}))
    proposal=sm.propose_mapping(request([a['id'],b['id']]))
    assert proposal['combination']['eligible'] is False and 'confidence' in proposal['combination']['blocked_reason']


def test_many_to_many_join_proposal_is_blocked(joined,monkeypatch):
    a,b,response=joined
    duplicate=save_rows([{'ref':'R1','category':'home'},{'ref':'R1','category':'tech'}],'duplicate.csv')
    reply=response();reply.datasets[1].dataset_id=duplicate['id'];reply.combination.right_dataset_id=duplicate['id']
    monkeypatch.setattr(sm,'structured',lambda *args:(reply,{}))
    proposal=sm.propose_mapping(request([a['id'],duplicate['id']]))
    assert proposal['combination']['eligible'] is False and 'unique' in proposal['combination']['blocked_reason']


def test_joint_dataset_omission_rejected(joined,monkeypatch):
    a,b,response=joined;reply=response();reply.datasets[1].dataset_id=a['id']
    monkeypatch.setattr(sm,'structured',lambda *args:(reply,{}))
    with pytest.raises(ValueError,match='omitted, duplicated or invented'):sm.propose_mapping(request([a['id'],b['id']]))


def test_declared_sensitive_samples_withheld():
    fields=sm.profile([{'opaque':'private fixture','token':'pii:hmac:v1:KEY:abc'}],{'opaque'})
    assert all(c['samples']==['[PII SAMPLE WITHHELD]'] for c in fields)


def test_preview_endpoint_preserves_sources_and_outputs(joined):
    a,b,_=joined
    with TestClient(app) as client:
        result=client.post('/api/datasets/merge/preview',json={'dataset_ids':[a['id'],b['id']],'mode':'inner_join','left_key':'ref','right_key':'ref'})
        assert result.status_code==200,result.text
        assert result.json()['dropped_left_rows']==1 and result.json()['output_rows']==1
        assert 'R1' not in result.text
    assert_no_outputs()


def test_explicit_append_cannot_be_replaced_with_join(joined):
    a,b,_=joined
    with pytest.raises(ValueError,match='explicit append'):sm.propose_mapping(request([a['id'],b['id']],'append'))
