import json
import httpx
import pytest
import yaml
from pydantic import BaseModel
from app import llm_router as router


class Result(BaseModel):
    answer:str


@pytest.fixture
def routing(tmp_path,monkeypatch):
    monkeypatch.setenv('PROVIDER','router')
    monkeypatch.setenv('TEST_KEY_A','fake-secret-a')
    monkeypatch.setenv('TEST_KEY_B','fake-secret-b')
    monkeypatch.setattr(router.time,'sleep',lambda _:None)
    path=tmp_path/'routes.yaml';monkeypatch.setenv('LLM_CONFIG_FILE',str(path))
    def configure(**updates):
        raw={'primary':'main','fallbacks':['backup'],'max_attempts_per_route':1,'routes':[{'name':'main','provider':'openai','model':'main-model','api_key_env':'TEST_KEY_A','vision':False},{'name':'backup','provider':'openai','model':'backup-model','api_key_env':'TEST_KEY_B','vision':True}]}
        raw.update(updates);path.write_text(yaml.safe_dump(raw),encoding='utf-8');return raw
    configure();return configure


def mock_client(monkeypatch,responses):
    calls=[]
    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            calls.append({'url':url,**kwargs})
            response=responses.pop(0)
            if isinstance(response,Exception):raise response
            return response
    monkeypatch.setattr(router.httpx,'Client',Client);return calls


def success(protocol='openai'):
    if protocol=='openai':return httpx.Response(200,json={'status':'completed','id':'response','output':[{'content':[{'type':'output_text','text':'{"answer":"ok"}'}]}]})
    if protocol=='anthropic':return httpx.Response(200,json={'stop_reason':'end_turn','content':[{'type':'text','text':'{"answer":"ok"}'}]})
    return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"answer":"ok"}'}}]})


@pytest.mark.parametrize('failure',[httpx.ConnectError('network'),httpx.ReadTimeout('timeout'),httpx.Response(429),httpx.Response(503),httpx.Response(404,json={'error':{'code':'model_not_found'}}),httpx.Response(429,json={'error':{'code':'insufficient_quota'}})])
def test_availability_fallback_selects_backup(routing,monkeypatch,failure):
    calls=mock_client(monkeypatch,[failure,success()])
    result,meta=router.structured('task','input',Result)
    assert result.answer=='ok' and meta['route']=='backup' and meta['fallback_used'] is True
    assert [c['json']['model'] for c in calls]==['main-model','backup-model']
    assert 'fake-secret' not in json.dumps(meta)


@pytest.mark.parametrize('status',[400,401,403,404])
def test_configuration_and_auth_errors_do_not_fallback(routing,monkeypatch,status):
    calls=mock_client(monkeypatch,[httpx.Response(status),success()])
    with pytest.raises(router.ModelError):router.structured('task','input',Result)
    assert len(calls)==1


@pytest.mark.parametrize('payload',[{'status':'incomplete'},{'status':'completed','output':[{'content':[{'type':'refusal','refusal':'no'}]}]},{'status':'completed','output':[{'content':[{'type':'output_text','text':'{"wrong":true}'}]}]}])
def test_refusal_incomplete_or_bad_output_stops_chain(routing,monkeypatch,payload):
    calls=mock_client(monkeypatch,[httpx.Response(200,json=payload),success()])
    with pytest.raises(router.ModelError):router.structured('task','input',Result)
    assert len(calls)==1


def test_retry_then_fallback_and_global_budget(routing,monkeypatch):
    routing(max_attempts_per_route=2,max_calls=3)
    calls=mock_client(monkeypatch,[httpx.Response(503),httpx.Response(503),success()])
    assert router.structured('task','input',Result)[1]['route']=='backup'
    assert len(calls)==3
    routing(max_attempts_per_route=2,max_calls=2)
    calls=mock_client(monkeypatch,[httpx.Response(503),httpx.Response(503),success()])
    with pytest.raises(router.ModelError,match='budget'):router.structured('task','input',Result)
    assert len(calls)==2


def test_vision_skips_text_only_route(routing,monkeypatch):
    calls=mock_client(monkeypatch,[success()])
    _,meta=router.structured('task','input',Result,image=b'png',mime='image/png')
    assert len(calls)==1 and calls[0]['json']['model']=='backup-model'
    assert meta['attempts'][0]['outcome']=='skipped_vision_unsupported'


def test_cross_provider_requires_explicit_authorization(routing,monkeypatch):
    raw=routing();raw['routes'][1]['provider']='gemini'
    routing(**raw)
    with pytest.raises(ValueError,match='cross-provider'):router.load_config()
    monkeypatch.setenv('LLM_ALLOW_CROSS_PROVIDER_FALLBACK','true')
    calls=mock_client(monkeypatch,[httpx.Response(503),success('chat')])
    _,meta=router.structured('task','input',Result)
    assert meta['provider']=='gemini'
    assert calls[1]['url']=='https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'


def test_anthropic_native_shape_and_validation(routing,monkeypatch):
    raw=routing();raw['routes'][0]['provider']='anthropic';raw['fallbacks']=[]
    routing(**raw)
    calls=mock_client(monkeypatch,[success('anthropic')])
    assert router.structured('task','input',Result)[0].answer=='ok'
    assert calls[0]['url']=='https://api.anthropic.com/v1/messages'
    assert calls[0]['headers']['x-api-key']=='fake-secret-a'
    assert calls[0]['json']['output_config']['format']['type']=='json_schema'


def test_compatible_endpoint_and_json_object_mode(routing,monkeypatch):
    raw=routing();raw['fallbacks']=[];raw['routes'][0].update(provider='openai_compatible',base_url='http://127.0.0.1:11434/v1',allow_http_loopback=True,structured_mode='json_object')
    routing(**raw)
    calls=mock_client(monkeypatch,[success('chat')])
    router.structured('task','input',Result)
    assert calls[0]['json']['response_format']=={'type':'json_object'}
    assert 'schema' in calls[0]['json']['messages'][0]['content']
    raw['routes'][0]['base_url']='http://untrusted.example/v1';routing(**raw)
    with pytest.raises(ValueError):router.load_config()


def test_manual_route_switch_and_status_redaction(routing,monkeypatch):
    monkeypatch.setenv('LLM_PRIMARY_ROUTE','backup');monkeypatch.setenv('LLM_FALLBACK_ROUTES','')
    calls=mock_client(monkeypatch,[success()])
    assert router.structured('task','input',Result)[1]['route']=='backup'
    status=router.public_config()
    assert status['primary']=='backup' and status['fallbacks']==[]
    assert 'fake-secret' not in json.dumps(status)


def test_missing_primary_configuration_can_use_authorized_backup(routing,monkeypatch):
    monkeypatch.delenv('TEST_KEY_A')
    calls=mock_client(monkeypatch,[success()])
    assert router.structured('task','input',Result)[1]['route']=='backup'
    assert len(calls)==1


def test_invalid_config_does_not_echo_secrets(routing,monkeypatch):
    raw=routing();raw['routes'][0]['api_key']='do-not-echo-this-secret';routing(**raw)
    with pytest.raises(ValueError) as exc:router.load_config()
    assert 'do-not-echo-this-secret' not in str(exc.value)


def test_cross_endpoint_compatible_routes_need_permission(routing):
    raw=routing()
    for route,host in zip(raw['routes'],['https://first.example/v1','https://second.example/v1']):
        route.update(provider='openai_compatible',base_url=host)
    routing(**raw)
    with pytest.raises(ValueError,match='cross-provider'):router.load_config()


@pytest.mark.parametrize('error',[{'code':404,'status':'NOT_FOUND'},{'type':'not_found_error'}])
def test_native_vendor_unavailable_envelopes_fail_over(routing,monkeypatch,error):
    calls=mock_client(monkeypatch,[httpx.Response(404,json={'error':error}),success()])
    assert router.structured('task','input',Result)[1]['route']=='backup'
    assert len(calls)==2


@pytest.mark.parametrize('vendor,endpoint',[('groq','https://api.groq.com/openai/v1/chat/completions'),('deepseek','https://api.deepseek.com/chat/completions')])
def test_named_chat_providers(monkeypatch,vendor,endpoint):
    monkeypatch.setenv('PROVIDER',vendor)
    monkeypatch.setenv(vendor.upper()+'_MODEL','account-model')
    monkeypatch.setenv(vendor.upper()+'_API_KEY','private-test-key')
    calls=mock_client(monkeypatch,[success('chat')])
    result,meta=router.structured('task','input',Result)
    assert result.answer=='ok' and meta['provider']==vendor
    assert calls[0]['url']==endpoint
    assert calls[0]['headers']['Authorization']=='Bearer private-test-key'
    assert calls[0]['json']['response_format']=={'type':'json_object'}
    assert 'JSON' in calls[0]['json']['messages'][0]['content']
    assert router.load_config().routes[0].vision is False
    assert 'private-test-key' not in json.dumps(meta)


def test_gemini_groq_deepseek_fallback_chain(routing,monkeypatch):
    routes=[{'name':v,'provider':v,'model':v+'-model','api_key_env':'TEST_KEY_A','structured_mode':'json_object'} for v in ['gemini','groq','deepseek']]
    routing(primary='gemini',fallbacks=['groq','deepseek'],routes=routes,allow_cross_provider_fallback=True)
    calls=mock_client(monkeypatch,[httpx.Response(429),httpx.Response(503),success('chat')])
    result,meta=router.structured('task','input',Result)
    assert result.answer=='ok' and meta['provider']=='deepseek' and meta['fallback_used'] is True
    assert [c['json']['model'] for c in calls]==['gemini-model','groq-model','deepseek-model']


@pytest.mark.parametrize('vendor',['groq','deepseek'])
def test_named_chat_invalid_json_stops_fallback(routing,monkeypatch,vendor):
    raw=routing();raw['routes'][0].update(provider=vendor,structured_mode='json_object')
    routing(**raw,allow_cross_provider_fallback=True)
    calls=mock_client(monkeypatch,[httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'   '}}]}),success()])
    with pytest.raises(router.ModelError):router.structured('task','input',Result)
    assert len(calls)==1


@pytest.mark.parametrize('vendor',['gemini','groq','deepseek','openai','anthropic'])
def test_common_model_selector_accepts_user_model_id(monkeypatch,vendor):
    monkeypatch.setenv('PROVIDER',vendor)
    monkeypatch.setenv(vendor.upper()+'_MODEL','previous-model')
    monkeypatch.setenv('LLM_MODEL','my-chosen-model/v2')
    assert router.load_config().routes[0].resolved_model()=='my-chosen-model/v2'


def test_compatible_endpoint_common_settings_and_local_no_key(monkeypatch):
    monkeypatch.setenv('PROVIDER','openai_compatible')
    monkeypatch.setenv('LLM_MODEL','my-local-model:latest')
    monkeypatch.setenv('LLM_BASE_URL','http://127.0.0.1:11434/v1')
    monkeypatch.setenv('LLM_ALLOW_HTTP_LOOPBACK','true')
    monkeypatch.setenv('LLM_REQUIRE_API_KEY','false')
    monkeypatch.setenv('LLM_VISION','false')
    monkeypatch.setenv('LLM_STRUCTURED_MODE','json_object')
    route=router.load_config().routes[0]
    assert route.resolved_model()=='my-local-model:latest'
    assert route.structured_mode=='json_object' and route.require_api_key is False and route.vision is False


def test_generic_key_fallback_preserves_vendor_key_priority(monkeypatch):
    monkeypatch.setenv('PROVIDER','groq');monkeypatch.setenv('GROQ_MODEL','chosen-model')
    monkeypatch.setenv('LLM_API_KEY','test-generic-key');monkeypatch.delenv('GROQ_API_KEY',raising=False)
    assert router.load_config().routes[0].api_key_env=='LLM_API_KEY'
    monkeypatch.setenv('GROQ_API_KEY','test-vendor-key')
    assert router.load_config().routes[0].api_key_env=='GROQ_API_KEY'


@pytest.mark.parametrize('failure', [
    httpx.Response(400, json={'error': {'code': 'json_validate_failed', 'failed_generation': 'private output'}}),
    httpx.Response(200, json={'status': 'completed', 'output': [
        {'content': [{'type': 'output_text', 'text': '{"wrong": true}'}]}
    ]}),
])
def test_mapping_output_is_retried_on_same_route(routing, monkeypatch, failure):
    routing(max_attempts_per_route=2)
    calls = mock_client(monkeypatch, [failure, success()])
    result, metadata = router.structured('task', 'input', Result, repair_invalid_output=True)
    assert result.answer == 'ok'
    assert [call['json']['model'] for call in calls] == ['main-model', 'main-model']
    assert 'private output' not in json.dumps(calls)
    assert [attempt['outcome'] for attempt in metadata['attempts']] == ['invalid_output', 'success']


@pytest.mark.parametrize('budget', [1, 3])
def test_mapping_repair_respects_budget_and_never_falls_back_on_invalid_output(routing, monkeypatch, budget):
    routing(max_attempts_per_route=3, max_calls=budget)
    failure = httpx.Response(400, json={'error': {'code': 'json_validate_failed'}})
    calls = mock_client(monkeypatch, [failure, failure, success()])
    with pytest.raises(router.ModelError) as caught:
        router.structured('task', 'input', Result, repair_invalid_output=True)
    assert caught.value.category == 'invalid_output'
    assert [call['json']['model'] for call in calls] == ['main-model'] * min(2, budget)


def test_result_correction_reuses_same_route_and_budget(routing, monkeypatch):
    routing(max_attempts_per_route=2)
    calls = mock_client(monkeypatch, [success(), success()])
    result, metadata = router.structured(
        'task', 'input', Result, result_correction=lambda result: 'Include every source.',
    )
    assert result.answer == 'ok'
    assert [attempt['outcome'] for attempt in metadata['attempts']] == ['incomplete_mapping', 'success']
    assert [call['json']['model'] for call in calls] == ['main-model', 'main-model']
    assert 'Include every source.' in calls[1]['json']['instructions']


def test_rate_limited_correction_keeps_original_result_for_review(routing, monkeypatch):
    routing(max_attempts_per_route=3)
    calls = mock_client(monkeypatch, [success(), httpx.Response(429)])
    result, metadata = router.structured(
        'task', 'input', Result, result_correction=lambda result: 'Include every source.',
    )
    assert result.answer == 'ok'
    assert [attempt['outcome'] for attempt in metadata['attempts']] == ['incomplete_mapping', 'rate_limit']
    assert [call['json']['model'] for call in calls] == ['main-model', 'main-model']


def test_rate_limited_mapping_retry_suggests_provider_wait_without_another_call(routing, monkeypatch):
    routing(max_attempts_per_route=3)
    invalid = httpx.Response(400, json={'error': {'code': 'json_validate_failed'}})
    calls = mock_client(monkeypatch, [invalid, httpx.Response(429, headers={'Retry-After': '45'}), success()])
    with pytest.raises(router.ModelError, match='Wait 45 seconds') as caught:
        router.structured('task', 'input', Result, repair_invalid_output=True)
    assert caught.value.category == 'rate_limit'
    assert [call['json']['model'] for call in calls] == ['main-model', 'main-model']
