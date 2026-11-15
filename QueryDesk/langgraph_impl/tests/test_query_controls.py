import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph_impl.cancellation import Cancellation, QueryCancelled, activate, interruptible
from langgraph_impl.config import ModelConfig
from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database
from langgraph_impl.workflow import LangGraphWorkflow
from langgraph_impl.tests.test_workflow import FixtureModel, READY, COUNT


class RoleModelTests(unittest.TestCase):
    def test_roles_inherit_and_override_cost_and_provider_independently(self):
        values={'LLM_ENDPOINT':'https://base.example/v1','LLM_API_KEY':'base-key','LLM_MODEL':'small',
                'LLM_SQL_ENDPOINT':'https://other.example/v1/responses','LLM_SQL_API_KEY':'other-key',
                'LLM_SQL_MODEL':'large','LLM_SQL_MAX_TOKENS':'1234','LLM_SQL_TIMEOUT_SECONDS':'12',
                'LLM_API_STYLE':'chat_completions'}
        with patch('langgraph_impl.config.dotenv_values',return_value={}):
            intent=ModelConfig.for_role('intent',environ=values)
            sql=ModelConfig.for_role('sql',model='ui-choice',environ=values)
        self.assertEqual((intent.model,intent.api_key,intent.max_tokens),('small','base-key',4000))
        self.assertEqual((sql.model,sql.api_key,sql.base_url,sql.api_style,sql.max_tokens,sql.timeout),
                         ('large','other-key','https://other.example/v1','responses',1234,12))

    def test_different_endpoint_cannot_inherit_shared_credentials(self):
        with patch('langgraph_impl.config.dotenv_values',return_value={}):
            with self.assertRaises(ValueError):
                ModelConfig.for_role('validation',environ={'LLM_API_KEY':'secret','LLM_MODEL':'small',
                    'LLM_VALIDATION_ENDPOINT':'https://other.example/v1'})
            with self.assertRaises(ValueError):ModelConfig.for_role('execution',environ={})


class CancellationTests(unittest.TestCase):
    def test_interrupts_actual_sqlite_read_and_unregisters_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.db';create_demo(path)
            db=Database(path,timeout=10)
            control=Cancellation();started=threading.Event();outcome=[]
            def run():
                try:
                    with activate(control),db.connection({'orders'}) as conn:
                        started.set()
                        conn.execute('SELECT sum(a.order_id+b.order_id+c.order_id+d.order_id+e.order_id+f.order_id) FROM orders a,orders b,orders c,orders d,orders e,orders f').fetchall()
                except Exception as error:outcome.append(error)
            thread=threading.Thread(target=run);thread.start()
            self.assertTrue(started.wait(2));control.event.set();control.signal();thread.join(3)
            self.assertFalse(thread.is_alive());self.assertTrue(outcome)
            self.assertEqual(control.callbacks,{})

    def test_cancel_before_binding_prevents_work_and_late_results_are_rejected(self):
        control=Cancellation();control.event.set()
        with activate(control):
            with self.assertRaises(QueryCancelled):
                with interruptible():self.fail('Cancelled operation started')
        control=Cancellation()
        with activate(control):
            with self.assertRaises(QueryCancelled):
                with interruptible():control.event.set()


class WorkerLimitTests(unittest.TestCase):
    def exercise(self,workers,cancel_queued=False):
        from fastapi import HTTPException
        from langgraph_impl.api import QueryJobs,QueryInput
        from langgraph_impl.persistence import ReviewStore
        with tempfile.TemporaryDirectory() as directory:
            release=threading.Event();started=threading.Event();both=threading.Event()
            seen=[];lock=threading.Lock()
            class Work:
                def advance(self,question,**kwargs):
                    with lock:
                        seen.append(question);started.set()
                        if len(seen)==2:both.set()
                    release.wait(3)
                    return {'result':{'status':'answered','answer':'Done'}}
            jobs=QueryJobs(lambda *args:Work(),ReviewStore(Path(directory)/'records.db'),workers=workers,max_pending=2)
            try:
                jobs.submit(QueryInput(question='First'),'owner','stamp')
                self.assertTrue(started.wait(2))
                second=jobs.submit(QueryInput(question='Second'),'owner','stamp')
                with self.assertRaises(HTTPException) as full:jobs.submit(QueryInput(question='Third'),'owner','stamp')
                self.assertEqual(full.exception.status_code,429)
                if cancel_queued:
                    self.assertEqual(second['status'],'queued')
                    self.assertEqual(jobs.cancel(second['id'])['status'],'failed')
                else:self.assertTrue(both.wait(2),'Configured workers did not execute concurrently')
            finally:
                release.set();jobs.pool.shutdown(wait=True);jobs.close();jobs.cancel_pool.shutdown(wait=True)
            self.assertEqual(len(seen),1 if cancel_queued else 2)

    def test_two_workers_are_bounded_and_run_concurrently(self):self.exercise(2)

    def test_cancelled_queued_job_never_starts(self):self.exercise(1,True)


class EditedSQLTests(unittest.TestCase):
    def setUp(self):
        directory=self.enterContext(tempfile.TemporaryDirectory())
        path=Path(directory)/'data.db';create_demo(path,expanded=False)
        self.db=Database(path)
        scripts={'intent':[READY],'schema':[{'tables':['orders'],'reason':'Count orders'}],
                 'columns':[],'sql':[COUNT],'validation':[{'approved':True,'feedback':'Correct'}]*3,
                 'response':[{'answer':'5','reason':'Count'}]}
        self.models={role:FixtureModel(calls) for role,calls in scripts.items()}
        self.flow=LangGraphWorkflow(self.db,model_factory=lambda role:self.models[role])

    def review(self):
        tables=self.flow.advance('How many orders?')
        return self.flow.advance('How many orders?',state=tables['state'],decision={'kind':'tables','tables':['orders']})

    def test_edit_requires_revalidation_and_fresh_approval_without_reading_rows(self):
        review=self.review()
        with patch.object(self.db,'execute',wraps=self.db.execute) as execute:
            revised=self.flow.advance('How many orders?',state=review['state'],decision={'kind':'edit_sql',
                'candidate':{'sql':'SELECT COUNT(*) AS n FROM orders','parameters':[]}})
            self.assertEqual(revised['waiting'],'sql');execute.assert_not_called()
            self.assertNotEqual(review['proposal']['fingerprint'],revised['proposal']['fingerprint'])
            done=self.flow.advance('How many orders?',state=revised['state'],decision={'kind':'sql','proposal':revised['proposal']})
            self.assertEqual(done['result']['status'],'answered');execute.assert_called_once()

    def test_unsafe_edit_fails_closed_without_regeneration_or_execution(self):
        review=self.review()
        with patch.object(self.db,'execute') as execute:
            outcome=self.flow.advance('How many orders?',state=review['state'],decision={'kind':'edit_sql',
                'candidate':{'sql':'DELETE FROM orders','parameters':[]}})
            self.assertEqual(outcome['result']['status'],'failed');execute.assert_not_called()
            self.assertEqual(len(self.models['sql'].contexts),1)

    def test_api_rejects_stale_approval_after_sql_edit(self):
        from fastapi.testclient import TestClient
        from langgraph_impl.api import create_app
        from langgraph_impl.tests.auth_helpers import sign_in_admin
        app=create_app(self.db,workflow_factory=lambda query:self.flow,record_path=self.db.filename.parent/'records.db')
        with TestClient(app) as client:
            sign_in_admin(client)
            key=client.post('/api/queries',json={'question':'How many orders?'}).json()['id']
            def waiting(status):
                for _ in range(200):
                    result=client.get('/api/queries/'+key).json()
                    if result['status']==status:return result
                    time.sleep(.01)
                self.fail(str(result))
            tables=waiting('awaiting_confirmation')['confirmation']
            client.post(f'/api/queries/{key}/confirm',json={'version':tables['version'],'tables':['orders']})
            old=waiting('awaiting_sql_approval')['sql_approval']
            with patch.object(self.db,'execute',wraps=self.db.execute) as execute:
                revision={'version':old['version'],'fingerprint':old['fingerprint'],'sql':'SELECT COUNT(*) AS n FROM orders','parameters':[]}
                self.assertEqual(client.post(f'/api/queries/{key}/revise-sql',json=revision).status_code,200)
                new=waiting('awaiting_sql_approval')['sql_approval'];execute.assert_not_called()
                self.assertNotEqual(new['version'],old['version'])
                self.assertEqual(client.post(f'/api/queries/{key}/approve-sql',json={'version':old['version'],'fingerprint':old['fingerprint'],'decision':'approve'}).status_code,409)
                self.assertEqual(client.post(f'/api/queries/{key}/approve-sql',json={'version':new['version'],'fingerprint':new['fingerprint'],'decision':'approve'}).status_code,200)
                waiting('completed');execute.assert_called_once()
