import json
import pytest
from app import semantic_mapping as sm
from app.ingest import save_rows
from app.models import demo_spec
from app.evaluations import seed_demo


def test_mapping_uses_optional_context_and_rejects_fake_citations(monkeypatch):
    seed_demo()
    data=save_rows([{'sku':'A','name':'Desk','length':'200cm','price':10,'currency':'USD','category':'furniture'}],'supplier')
    paths=['sku','name','length','price','currency','category']
    monkeypatch.setenv('PROVIDER','openai')
    observed=[]
    def fake(task,payload,schema):
        context=json.loads(payload); observed.append(context)
        citations=[h['chunk_id'] for h in context['approved_knowledge'][:1]]
        return sm.SemanticResponse(mappings=[sm.SemanticMatch(target=c.name,source=p,confidence=.95,reason='Fixture match',evidence=[],knowledge_citations=citations) for c,p in zip(demo_spec().columns,paths)],warnings=[]),{'provider':'mock'}
    monkeypatch.setattr(sm,'structured',fake)
    req=sm.MappingRequest(instructions='Map product length units using the approved normalization rule',spec=demo_spec(),dataset_ids=[data['id']])
    without=sm.propose_mapping(req)
    assert observed[-1]['approved_knowledge']==[]
    req.use_knowledge=True
    with_rag=sm.propose_mapping(req)
    assert observed[-1]['approved_knowledge'] and with_rag['retrieved_knowledge']
    original=fake
    def hallucinate(*args):
        response,meta=original(*args); response.mappings[0].knowledge_citations=['invented']; return response,meta
    monkeypatch.setattr(sm,'structured',hallucinate)
    with pytest.raises(ValueError,match='not retrieved'): sm.propose_mapping(req)
