import json
import pytest
from app import store,notifications


def queued():
    with store.db() as conn:
        store.audit(conn,None,'pipeline.version_activated',{'pipeline_id':'demo','revision':2,'prompt':'secret source content','credentials':'never email'})
    with store.db() as conn: return dict(conn.execute('SELECT * FROM notification_outbox').fetchone())


def test_outbox_atomic_and_filters_sensitive_fields():
    row=queued(); payload=json.loads(row['payload_json'])
    assert payload['details']=={'pipeline_id':'demo','revision':2}
    with pytest.raises(ValueError):
        with store.db() as conn:
            store.audit(conn,None,'pipeline.version_rolled_back',{'revision':3})
            raise ValueError('transaction rolled back')
    with store.db() as conn: assert conn.execute('SELECT count(*) FROM notification_outbox').fetchone()[0]==1


def test_disabled_email_never_delivers(monkeypatch):
    queued()
    monkeypatch.setattr(notifications,'send',lambda *a:pytest.fail('disabled mail must not send'))
    assert notifications.dispatch_one() is None


def test_smtp_failure_keeps_change_and_retries(monkeypatch):
    row=queued(); monkeypatch.setenv('EMAIL_ENABLED','true')
    def fail(*args): raise OSError('credentials should not appear in persisted exception')
    monkeypatch.setattr(notifications,'send',fail)
    notifications.dispatch_one()
    with store.db() as conn: failed=dict(conn.execute('SELECT * FROM notification_outbox').fetchone())
    assert failed['status']=='pending' and failed['attempts']==1
    assert 'credentials' not in failed['last_error']
    with store.db() as conn: conn.execute('UPDATE notification_outbox SET available_at=0')
    sent=[]; monkeypatch.setattr(notifications,'send',lambda payload,key:sent.append(key))
    notifications.dispatch_one()
    with store.db() as conn: assert conn.execute('SELECT status FROM notification_outbox').fetchone()[0]=='sent'
    assert notifications.dispatch_one() is None and sent==[row['id']]


def test_configuration_failure_is_visible_and_retryable_manually(monkeypatch):
    row=queued(); monkeypatch.setenv('EMAIL_ENABLED','true')
    monkeypatch.setenv('SMTP_HOST','')
    notifications.dispatch_one()
    with store.db() as conn: assert conn.execute('SELECT status FROM notification_outbox').fetchone()[0]=='blocked'
    assert notifications.retry(row['id'])['status']=='pending'


def test_smtp_enforces_tls_and_sends_metadata_only(monkeypatch):
    for key,value in {'SMTP_HOST':'smtp.example.test','SMTP_PORT':'587','SMTP_TLS_MODE':'starttls','EMAIL_FROM':'etl@example.test','EMAIL_TO':'owner@example.test','SMTP_USERNAME':'user','SMTP_PASSWORD':'fake-password'}.items(): monkeypatch.setenv(key,value)
    actions=[]
    class SMTP:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def ehlo(self): actions.append('ehlo')
        def starttls(self,**kwargs): actions.append('tls')
        def login(self,*args): actions.append('login')
        def send_message(self,message): actions.append(message); return {}
    monkeypatch.setattr(notifications.smtplib,'SMTP',SMTP)
    row=queued(); notifications.send(json.loads(row['payload_json']),row['id'])
    assert actions.index('tls')<actions.index('login')
    email=actions[-1].as_string()
    assert 'fake-password' not in email and 'secret source content' not in email
    assert 'owner@example.test' in email
