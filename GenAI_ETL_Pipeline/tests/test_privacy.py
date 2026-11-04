import base64
import json
import pytest
from fastapi.testclient import TestClient
from app import store,privacy,llm_router
from app.main import app
from app.models import PipelineSpec,RunRequest
from app.ingest import ingest_bytes
from app.engine import execute


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv('PII_KEY_PROVIDER','environment')
    monkeypatch.setenv('PII_ENCRYPTION_KEY',base64.b64encode(b'e'*32).decode())
    monkeypatch.setenv('PII_HMAC_KEY',base64.b64encode(b'h'*32).decode())
    return monkeypatch


def pii_spec(action='encrypt',key_ref='PII_ENCRYPTION_KEY',**updates):
    raw={'target':'contacts','primary_key':'id','max_reject_ratio':0,'columns':[{'name':'id','required':True},{'name':'email','required':True,'transforms':['trim','lower'],'classification':'pii','protection':action,'key_ref':key_ref},{'name':'amount','dtype':'number'}]}
    raw.update(updates);return PipelineSpec.model_validate(raw)


def test_column_cipher_randomized_authenticated_and_typed(keys):
    a=privacy.encrypt('person@example.test','PII_ENCRYPTION_KEY',context='email');b=privacy.encrypt('person@example.test','PII_ENCRYPTION_KEY',context='email')
    assert a!=b and privacy.decrypt(a,'email')=='person@example.test'
    with pytest.raises(ValueError):privacy.decrypt(a,'another_column')
    keys.setenv('PII_ENCRYPTION_KEY',base64.b64encode(b'x'*32).decode())
    with pytest.raises(ValueError):privacy.decrypt(a,'email')


def test_pii_bronze_only_sensitive_column_encrypted_no_raw_copy(keys):
    raw=b'id,email,amount\n1,person@example.test,25\n'
    d=ingest_bytes(raw,'contacts.csv');saved=store.get_dataset(d['id'])
    assert saved['rows'][0]['id']=='1' and saved['rows'][0]['amount']=='25'
    assert saved['rows'][0]['email'].startswith(privacy.PREFIX)
    assert d['metadata']['bronze']['representation']=='protected_columns_json'
    with store.db() as conn:
        original=conn.execute('SELECT content FROM bronze_objects WHERE dataset_id=?',(d['id'],)).fetchone()[0]
        extract=conn.execute('SELECT content FROM extraction_batches').fetchone()[0]
        assert b'person@example.test' not in original and b'person@example.test' not in extract
        assert b'"amount": "25"' in original


def test_sensitive_source_paths_keeps_plaintext_pii_fields_policy_protected():
    assert privacy.sensitive_source_paths({
        'privacy': {
            'protected_columns': [],
            'sensitive_columns': ['Customer_Name'],
            'mode': 'plaintext_local',
        },
    }) == {'Customer_Name'}
    assert privacy.sensitive_source_paths({
        'privacy': {'protected_columns': ['email']},
    }) == {'email'}


def test_missing_key_fails_closed_without_raw_persistence(monkeypatch):
    monkeypatch.delenv('PII_ENCRYPTION_KEY',raising=False);monkeypatch.setenv('PII_KEY_PROVIDER','environment')
    with pytest.raises(ValueError,match='Privacy key'):ingest_bytes(b'id,email\n1,person@example.test','x.csv')
    with store.db() as conn:
        assert conn.execute('SELECT count(*) FROM datasets').fetchone()[0]==0
        assert conn.execute('SELECT content FROM extraction_batches').fetchone()[0]==b''


@pytest.mark.parametrize('action,key_ref',[('encrypt','PII_ENCRYPTION_KEY'),('mask','PII_ENCRYPTION_KEY'),('hmac_sha256','PII_HMAC_KEY')])
def test_silver_protection_and_no_diagnostic_plaintext(keys,action,key_ref):
    d=ingest_bytes(b'id,email,amount\n1,PERSON@example.test,25\n','contacts.csv')
    r=execute(RunRequest(spec=pii_spec(action,key_ref),dataset_ids=[d['id']],idempotency_key='privacy'))
    assert r['status']=='succeeded'
    encoded=json.dumps(r);assert 'PERSON@example.test' not in encoded and 'person@example.test' not in encoded
    value=r['report']['preview'][0]['record']['email']
    if action=='encrypt':assert privacy.decrypt(value,'email')=='person@example.test'
    if action=='mask':assert value=='[REDACTED]'
    if action=='hmac_sha256':assert value.startswith('pii:hmac:v1:')


def test_plaintext_target_policy_blocked(keys):
    d=ingest_bytes(b'id,email\n1,person@example.test','x.csv')
    s=PipelineSpec.model_validate({'primary_key':'id','columns':[{'name':'id','required':True},{'name':'contact','aliases':['email']}]})
    r=execute(RunRequest(spec=s,dataset_ids=[d['id']],idempotency_key='unsafe'))
    assert r['status']=='quality_failed' and r['report']['rejected']==1
    assert 'person@example.test' not in json.dumps(r)


def test_step_up_decryption_and_metadata_only_audit(keys):
    d=ingest_bytes(b'id,email,amount\n1,person@example.test,25','x.csv')
    r=execute(RunRequest(spec=pii_spec(),dataset_ids=[d['id']],idempotency_key='encrypted'))
    with TestClient(app) as client:
        request={'run_id':r['id'],'column':'email','row_numbers':[1]}
        assert client.post('/api/privacy/decrypt',json=request).status_code==403
        keys.setenv('PII_DECRYPT_TOKEN','step-up-token-'+'x'*32)
        result=client.post('/api/privacy/decrypt',json=request,headers={'X-PII-Access-Token':'step-up-token-'+'x'*32})
        assert result.status_code==200,result.text
        assert result.json()['rows'][0]['value']=='person@example.test'
    with store.db() as conn:
        audit=conn.execute("SELECT details_json FROM events WHERE kind='privacy.column_decrypted'").fetchone()[0]
        assert 'person@example.test' not in audit and 'row_count' in audit


def test_ai_guard_blocks_before_network(keys,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Network should not be called')
    monkeypatch.setattr(llm_router.httpx,'Client',forbidden)
    with pytest.raises(ValueError,match='personal data'):llm_router.structured('task','email=person@example.test',PipelineSpec)
    from app.semantic_mapping import profile
    p=profile([{'email':'person@example.test','product_name':'Desk'}])
    assert p[0]['samples']==['[PII SAMPLE WITHHELD]']
    assert 'person@example.test' not in json.dumps(p)


def test_keyed_tokens_stable_for_joins_and_random_cipher_not_keys(keys):
    from app.cleaning import clean_value
    column=pii_spec('hmac_sha256','PII_HMAC_KEY').columns[1]
    a=clean_value('Person@example.test',column)[0];b=clean_value('person@example.test',column)[0];assert a==b
    raw=pii_spec().model_dump();raw['primary_key']='email'
    with pytest.raises(ValueError,match='stable'):PipelineSpec.model_validate(raw)


def test_pii_joins_reencrypt_with_new_column_context(keys):
    from app.medallion_api import merge,MergeRequest
    a=ingest_bytes(b'id,email\n1,person@example.test','a.csv')
    b=ingest_bytes(b'id,category\n1,tech','b.csv')
    d=merge(MergeRequest(dataset_ids=[a['id'],b['id']],mode='left_join',left_key='id',right_key='id'))
    rows=store.get_dataset(d['id'])['rows'];assert rows[0]['left']['email'].startswith(privacy.PREFIX)
    assert privacy.decrypt_source(rows)[0]['left']['email']=='person@example.test'
