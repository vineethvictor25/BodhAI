import pytest
from fastapi.testclient import TestClient
from app import semantic_mapping as sm
from app.engine import execute, ConflictError
from app.ingest import save_rows
from app.models import demo_spec, RunRequest
from app.main import app


@pytest.fixture
def semantic_fixture(monkeypatch):
    monkeypatch.setenv("PROVIDER","openai")
    data=save_rows([{"merch_ref":"A-1","display_caption":"Oak table","package_span":"200cm","selling_value":"$20.00","settlement_ccy":"USD","department_label":"Furniture"}],"semantic.csv")
    spec=demo_spec()
    spec.columns[3].transforms=["currency_number"]
    paths=["merch_ref","display_caption","package_span","selling_value","settlement_ccy","department_label"]
    def fake(task,payload,schema):
        import json
        context=json.loads(payload)
        assert context["user_instructions"] == "Map supplier merchandise fields using the sample values"
        assert context["source"]["columns"][0]["samples"]
        return sm.SemanticResponse(mappings=[sm.SemanticMatch(target=c.name,source=p,confidence=.95,reason="Matched business meaning from sample values",evidence=["Oak table"]) for c,p in zip(spec.columns,paths)],warnings=[]), {"provider":"openai","model":"mock"}
    monkeypatch.setattr(sm,"structured",fake)
    request=sm.MappingRequest(instructions="Map supplier merchandise fields using the sample values",spec=spec,dataset_ids=[data["id"]])
    return request,data,paths


def decisions(proposal):
    return {d["dataset_id"]:{m["target"]:m["source"] for m in d["mappings"]} for d in proposal["datasets"]}


def test_semantic_approval_used_by_engine(semantic_fixture):
    request,data,paths=semantic_fixture
    proposal=sm.propose_mapping(request)
    run=RunRequest(spec=request.spec,dataset_ids=request.dataset_ids,idempotency_key="semantic",approved_mapping_id=proposal["id"])
    with pytest.raises(ValueError,match="approved"):
        execute(run)
    approval=sm.MappingApproval(approved=True,decisions=decisions(proposal))
    sm.approve_mapping(proposal["id"],approval)
    assert sm.approve_mapping(proposal["id"],approval)["status"] == "approved"
    result=execute(run)
    assert result["status"] == "succeeded"
    record=result["report"]["preview"][0]["record"]
    assert record["product_id"] == "A-1" and record["length_m"] == 2 and record["price"] == 20
    assert result["report"]["mappings"][0]["columns"][0]["proposal_id"] == proposal["id"]
    assert execute(run)["id"] == result["id"]
    run.approved_mapping_id=None
    with pytest.raises(ConflictError): execute(run)


@pytest.mark.parametrize("fault",["invented","duplicate","missing"])
def test_invalid_ai_mappings_rejected(semantic_fixture,monkeypatch,fault):
    request,_,paths=semantic_fixture
    original=sm.structured
    def broken(*args):
        response,meta=original(*args)
        if fault=="invented": response.mappings[0].source="not_in_source"
        elif fault=="duplicate": response.mappings[1].source=paths[0]
        else: response.mappings.pop()
        return response,meta
    monkeypatch.setattr(sm,"structured",broken)
    with pytest.raises(ValueError): sm.propose_mapping(request)


def test_low_confidence_requires_explicit_selection(semantic_fixture,monkeypatch):
    request,_,paths=semantic_fixture
    original=sm.structured
    def uncertain(*args):
        response,meta=original(*args); response.mappings[0].confidence=.4; return response,meta
    monkeypatch.setattr(sm,"structured",uncertain)
    proposal=sm.propose_mapping(request)
    assert proposal["datasets"][0]["mappings"][0]["source"] is None
    choices=decisions(proposal)
    with pytest.raises(ValueError,match="required"):
        sm.approve_mapping(proposal["id"],sm.MappingApproval(approved=True,decisions=choices))
    choices[request.dataset_ids[0]]["product_id"]=paths[0]
    sm.approve_mapping(proposal["id"],sm.MappingApproval(approved=True,decisions=choices))


def test_approval_scope_and_immutability(semantic_fixture):
    request,data,_=semantic_fixture
    proposal=sm.propose_mapping(request)
    choices=decisions(proposal)
    sm.approve_mapping(proposal["id"],sm.MappingApproval(approved=True,decisions=choices))
    changed=request.spec.model_copy(deep=True); changed.target="other"
    from app import store
    with pytest.raises(ValueError,match="schema changed"):
        sm.resolve_approved(proposal["id"],changed,[store.get_dataset(data["id"])])
    other=save_rows([{"merch_ref":"B"}],"other")
    with pytest.raises(ValueError,match="snapshots"):
        sm.resolve_approved(proposal["id"],request.spec,[store.get_dataset(other["id"])])
    choices[data["id"]]["category"]=None
    with pytest.raises(ValueError,match="immutable"):
        sm.approve_mapping(proposal["id"],sm.MappingApproval(approved=True,decisions=choices))


def test_semantic_api_flow(semantic_fixture):
    request,data,_=semantic_fixture
    with TestClient(app) as client:
        response=client.post("/api/mappings/generate",json=request.model_dump())
        assert response.status_code==200
        proposal=response.json()
        assert client.post(f'/api/mappings/{proposal["id"]}/approve',json={"approved":False,"decisions":decisions(proposal)}).status_code==422
        approved=client.post(f'/api/mappings/{proposal["id"]}/approve',json={"approved":True,"decisions":decisions(proposal)})
        assert approved.status_code==200
        assert client.get(f'/api/mappings/{proposal["id"]}').json()["status"]=="approved"
        result=client.post('/api/runs',json={"spec":request.spec.model_dump(),"dataset_ids":request.dataset_ids,"idempotency_key":"http-semantic","approved_mapping_id":proposal["id"]})
        assert result.status_code==200 and result.json()["status"]=="succeeded"


def test_profiles_are_bounded():
    columns=sm.profile([{"nested":{"opaque":"x"*1000},"nullable":None},{"nested":{"opaque":"y"},"other":1}])
    assert max(len(v) for c in columns for v in c["samples"])<=80
    assert next(c for c in columns if c["path"]=="nullable")["missing"]==1


def test_local_mode_has_no_fake_ai_mapping():
    data=save_rows([{"id":1}],"offline")
    with pytest.raises(ValueError,match="requires PROVIDER"):
        sm.propose_mapping(sm.MappingRequest(instructions="Map these source columns",spec=demo_spec(),dataset_ids=[data["id"]]))
