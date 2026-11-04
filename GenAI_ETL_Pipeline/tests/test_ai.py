import json
import httpx
import pytest
from app import ai
from app.models import demo_spec, PipelineSpec


def configure(monkeypatch):
    monkeypatch.setenv("PROVIDER","openai")
    monkeypatch.setenv("OPENAI_API_KEY","fake-test-key")
    monkeypatch.setenv("OPENAI_MODEL","test-model")
    monkeypatch.setattr(ai.time,"sleep",lambda _: None)


def test_live_adapter_validates_output_and_retries(monkeypatch):
    configure(monkeypatch)
    calls = []
    class FakeClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,url,**kwargs):
            calls.append(kwargs["json"])
            if len(calls) == 1:
                return httpx.Response(429)
            return httpx.Response(200,json={"status":"completed","id":"r1","output":[{"content":[{"type":"output_text","text":demo_spec().model_dump_json()}]}]})
    monkeypatch.setattr(ai.httpx,"Client",FakeClient)
    result, metadata = ai.structured("Create a plan","test",PipelineSpec)
    assert result.primary_key == "product_id"
    assert len(calls) == 2
    assert calls[0]["store"] is False
    assert metadata["response_id"] == "r1"


@pytest.mark.parametrize("payload",[{"status":"incomplete"},{"status":"completed","output":[{"content":[{"type":"output_text","text":"{\"bad\":true}"}]}]}])
def test_invalid_ai_output_fails_closed(monkeypatch,payload):
    configure(monkeypatch)
    class FakeClient:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,*args,**kwargs): return httpx.Response(200,json=payload)
    monkeypatch.setattr(ai.httpx,"Client",FakeClient)
    with pytest.raises(ValueError):
        ai.structured("task","data",PipelineSpec)


def test_quota_exhaustion_does_not_retry(monkeypatch):
    configure(monkeypatch)
    calls=[]
    class FakeClient:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,*args,**kwargs):
            calls.append(1)
            return httpx.Response(429,json={"error":{"code":"insufficient_quota"}})
    monkeypatch.setattr(ai.httpx,"Client",FakeClient)
    with pytest.raises(ValueError,match="quota or billing"):
        ai.structured("task","data",PipelineSpec)
    assert len(calls)==1
