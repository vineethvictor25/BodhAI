import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from langgraph_impl.api import QueryJobs,QueryInput,TableConfirmation,SQLDecision,create_app
from langgraph_impl.demo import create_demo
from langgraph_impl.persistence import ReviewStore
from langgraph_impl.pii import LocalCipher,initialize_key,PREFIX
from langgraph_impl.storage import Database
from langgraph_impl.workflow import LangGraphWorkflow
from langgraph_impl.tests.test_nonblocking import DeterministicModel
from langgraph_impl.tests.auth_helpers import sign_in_admin,PASSWORD


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        create_demo(self.path/'sales.db',expanded=False)
        self.db=Database(self.path/'sales.db')
        self.store=ReviewStore(self.path/'records.db')
        self.calls=[];self.closed_models=0;self.revoked=False
        self.jobs=self.manager()

    def factory(self,*args):return LangGraphWorkflow(self.db,model_factory=lambda role:DeterministicModel(self))
    def guard(self,*args):
        if self.revoked:raise PermissionError('Revoked')
    def manager(self,store=None):
        jobs=QueryJobs(self.factory,store or self.store,guard=self.guard,confirmation_timeout=30)
        self.addCleanup(jobs.close);return jobs
    def wait(self,key,status):
        for _ in range(600):
            job=self.jobs.public(key)
            if job['status']==status:return job
            if job['status']=='failed' and status!='failed':self.fail(str(job))
            time.sleep(.005)
        self.fail(str(job))
    def tables(self,key):
        proposal=self.wait(key,'awaiting_confirmation')['confirmation']
        self.jobs.confirm(key,TableConfirmation(version=proposal['version'],tables=['orders']))
    def sql_decision(self,key):
        proposal=self.wait(key,'awaiting_sql_approval')['sql_approval']
        return SQLDecision(version=proposal['version'],fingerprint=proposal['fingerprint'],decision='approve')
    def start(self,sql=False):
        key=self.jobs.submit(QueryInput(question='How many orders?'),'owner','1:1')['id']
        self.wait(key,'awaiting_confirmation')
        if sql:self.tables(key);self.sql_decision(key)
        return key
    def restart(self):
        self.jobs.close()
        self.store=ReviewStore(self.store.filename,cipher=self.store.cipher)
        self.jobs=self.manager()

    def test_table_checkpoint_survives_restart_and_completes_real_query(self):
        key=self.start();old=self.jobs.public(key)['confirmation']['version']
        self.restart()
        self.assertEqual(self.jobs.public(key)['status'],'recoverable')
        restored=self.jobs.restore_review(key)
        self.assertNotEqual(restored['confirmation']['version'],old)
        self.assertEqual(len(self.calls),2)
        self.tables(key)
        self.jobs.approve_sql(key,self.sql_decision(key))
        result=self.wait(key,'completed')['result']
        self.assertEqual(result['queries'][0]['result']['rows'],[[5]])
        self.assertEqual(result['model_calls'],6)
        self.assertIsNone(self.store.progress(key))

    def test_sql_restore_rechecks_plan_without_models_and_requires_new_approval(self):
        key=self.start(sql=True);old=self.sql_decision(key)
        deadline=self.jobs.jobs[key]['sql_approval']['deadline']
        self.restart()
        with patch.object(self.db,'execute') as execute:
            restored=self.jobs.restore_review(key)
            self.assertEqual(len(self.calls),5)
            execute.assert_not_called()
        self.assertEqual(restored['sql_approval']['deadline'],deadline)
        self.assertNotEqual(restored['sql_approval']['version'],old.version)
        with self.assertRaises(HTTPException):self.jobs.approve_sql(key,old)
        with self.assertRaises(HTTPException):self.jobs.restore_review(key)
        self.jobs.approve_sql(key,self.sql_decision(key))
        self.assertEqual(self.wait(key,'completed')['result']['model_calls'],6)

    def test_accepted_but_queued_sql_is_restored_for_review_not_auto_execution(self):
        key=self.start(sql=True);old=self.sql_decision(key)
        started,release=threading.Event(),threading.Event()
        def occupy():started.set();release.wait(3)
        blocker=self.jobs.pool.submit(occupy);self.assertTrue(started.wait(1))
        try:
            self.jobs.approve_sql(key,old)
            saved=self.store.progress(key)
            self.assertEqual(saved['phase'],'queued')
            self.assertNotIn('decision',saved)
            self.jobs.close()
        finally:release.set();blocker.result(timeout=2)
        self.jobs=self.manager()
        with patch.object(self.db,'execute') as execute:
            self.assertEqual(self.jobs.public(key)['status'],'recoverable')
            restored=self.jobs.restore_review(key)
            self.assertEqual(restored['status'],'awaiting_sql_approval')
            execute.assert_not_called()
        with self.assertRaises(HTTPException):self.jobs.approve_sql(key,old)

    def test_running_or_executing_checkpoints_are_never_replayed(self):
        for phase in ('running','executing'):
            key=self.start(sql=True)
            checkpoint=self.store.progress(key);checkpoint['phase']=phase
            self.store.save_query(self.store.query(key),progress=checkpoint)
            self.restart()
            with patch.object(self.db,'execute') as execute:
                self.assertEqual(self.jobs.public(key)['status'],'failed')
                with self.assertRaises(HTTPException):self.jobs.restore_review(key)
                execute.assert_not_called()
            self.assertIsNone(self.store.progress(key))

    def test_execution_marker_is_durable_before_the_first_source_read(self):
        key=self.start(sql=True);original=self.db.execute;observed=[]
        def execute(*args,**kwargs):
            observed.append(self.store.progress_metadata(key)['phase'])
            return original(*args,**kwargs)
        with patch.object(self.db,'execute',side_effect=execute):
            self.jobs.approve_sql(key,self.sql_decision(key))
            self.wait(key,'completed')
        self.assertEqual(observed,['executing'])

    def test_revocation_and_changed_schema_or_limits_block_restore(self):
        key=self.start(sql=True);self.restart()
        self.revoked=True
        with self.assertRaises(PermissionError):self.jobs.restore_review(key)
        self.revoked=False;self.db.row_limit+=1
        with self.assertRaises(ValueError):self.jobs.restore_review(key)
        self.db.row_limit-=1
        self.jobs.factory=lambda *args:LangGraphWorkflow(self.db,max_model_calls=99)
        with self.assertRaises(ValueError):self.jobs.restore_review(key)
        self.assertNotIn(key,self.jobs.jobs)

    def test_expiry_and_delete_cascade_and_forbid_late_checkpoint_resurrection(self):
        key=self.start();checkpoint=self.store.progress(key);job=self.store.query(key)
        self.store.delete_query(key);self.jobs.erase([key])
        self.assertIsNone(self.store.progress(key))
        self.assertFalse(self.store.save_query(job,progress=checkpoint))
        with self.store.connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM query_progress').fetchone()[0],0)
        other=self.start()
        self.jobs.close()
        self.store.clock=lambda:time.time()+2*86400
        self.assertIsNone(self.store.progress(other))
        self.store.maintenance()
        with self.store.connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM query_progress').fetchone()[0],0)

    def test_expired_or_bad_budget_checkpoint_is_not_restorable(self):
        key=self.start(sql=True)
        checkpoint=self.store.progress(key);checkpoint['state']['budget_calls']=999
        self.store.save_query(self.store.query(key),progress=checkpoint)
        self.restart()
        with self.assertRaises(ValueError):self.jobs.restore_review(key)
        checkpoint['deadline']=time.time()-1
        self.store.save_query(self.store.query(key),progress=checkpoint)
        self.assertEqual(self.jobs.public(key)['status'],'failed')
        with self.assertRaises(HTTPException):self.jobs.restore_review(key)

    def test_snapshot_and_checkpoint_write_are_atomic(self):
        key=self.start();before=self.store.query(key)
        checkpoint=self.store.progress(key);checkpoint['phase']='invalid'
        changed={**before,'question':'Should roll back'}
        with self.assertRaises(ValueError):self.store.save_query(changed,progress=checkpoint)
        self.assertEqual(self.store.query(key)['question'],before['question'])
        self.assertEqual(self.store.progress(key)['phase'],'waiting')

    def test_incomplete_state_is_rejected_before_any_execution(self):
        from copy import deepcopy
        key=self.start(sql=True)
        original=self.store.progress(key)
        self.restart()
        for field in ('queries','intent_info','node_path'):
            checkpoint=deepcopy(original);checkpoint['state'].pop(field)
            self.store.save_query(self.store.query(key),progress=checkpoint)
            with patch.object(self.db,'execute') as execute:
                with self.assertRaises(ValueError):self.jobs.restore_review(key)
                execute.assert_not_called()

    def test_encrypted_checkpoints_roundtrip_and_wrong_key_fails_closed(self):
        key=self.start(sql=True);self.jobs.close()
        initialize_key(self.path/'key')
        cipher=LocalCipher(self.path/'key',True)
        encrypted=ReviewStore(self.store.filename,cipher=cipher)
        with encrypted.connect() as conn:
            payload=conn.execute('SELECT payload FROM query_progress WHERE query_id=?',(key,)).fetchone()[0]
        self.assertTrue(payload.startswith(PREFIX));self.assertNotIn('How many orders',payload)
        self.jobs=self.manager(encrypted)
        self.assertEqual(self.jobs.restore_review(key)['status'],'awaiting_sql_approval')
        self.jobs.close()
        initialize_key(self.path/'wrong')
        wrong=ReviewStore(self.store.filename,cipher=LocalCipher(self.path/'wrong',True))
        with self.assertRaises(ValueError):wrong.progress(key)

    def test_restore_api_requires_owner_csrf_and_never_accepts_old_approval(self):
        record_path=self.path/'api-records.db'
        with patch('langgraph_impl.api.LangChainModel',side_effect=lambda *args,**kwargs:DeterministicModel(self)):
            app=create_app(self.db,record_path=record_path,security_values={'PII_ENCRYPTION_ENABLED':'false'})
            with TestClient(app) as client:
                sign_in_admin(client)
                key=client.post('/api/queries',json={'question':'How many orders?'}).json()['id']
                for _ in range(300):
                    job=client.get('/api/queries/'+key).json()
                    if job['status']=='awaiting_confirmation':break
                    time.sleep(.005)
                self.assertEqual(job['status'],'awaiting_confirmation')
            restarted=create_app(self.db,record_path=record_path,security_values={'PII_ENCRYPTION_ENABLED':'false'})
            with TestClient(restarted) as admin,TestClient(restarted) as member:
                sign_in_admin(admin)
                registered=member.post('/api/auth/register',json={'username':'analyst','password':PASSWORD,'source':'sample','domain':'sales'}).json()
                member.headers['X-CSRF-Token']=registered['csrf_token']
                self.assertEqual(member.post(f'/api/queries/{key}/restore-review',json={}).status_code,404)
                token=admin.headers.pop('X-CSRF-Token')
                self.assertEqual(admin.post(f'/api/queries/{key}/restore-review',json={}).status_code,403)
                admin.headers['X-CSRF-Token']=token
                self.assertEqual(admin.get('/api/queries/'+key).json()['status'],'recoverable')
                self.assertEqual(admin.post(f'/api/queries/{key}/restore-review',json={'decision':'approve','state':{}}).status_code,422)
                restored=admin.post(f'/api/queries/{key}/restore-review',json={})
                self.assertEqual(restored.status_code,200,restored.text)
                self.assertEqual(restored.json()['status'],'awaiting_confirmation')
                self.assertNotIn('checkpoint',restored.json())
                self.assertEqual(admin.post(f'/api/queries/{key}/restore-review',json={}).status_code,409)
