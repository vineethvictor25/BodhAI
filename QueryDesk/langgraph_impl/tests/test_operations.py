from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Request,Response
from langchain_core.messages import AIMessage
from openai import APIConnectionError,APIStatusError

from langgraph_impl.api import QueryJobs,QueryInput,create_app
from langgraph_impl.config import ModelConfig
from langgraph_impl.contracts import IntentOutput
from langgraph_impl.demo import create_demo
from langgraph_impl.models import LangChainModel
from langgraph_impl.persistence import ReviewStore
from langgraph_impl.storage import Database
from langgraph_impl.telemetry import Metrics,observe,stage
from langgraph_impl.tests.auth_helpers import sign_in_admin,approved_model_policy
from langgraph_impl.load_test import run_load


class IdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.directory=self.enterContext(tempfile.TemporaryDirectory());self.root=Path(self.directory)
        self.store=ReviewStore(self.root/'records.db')
        self.started=threading.Event();self.release=threading.Event();self.calls=[]
        owner=self
        class Work:
            def advance(self,question,**kwargs):
                owner.calls.append(question);owner.started.set();owner.release.wait(4)
                return {'result':{'status':'answered','answer':'Synthetic'}}
        self.factory=lambda *args:Work()
        self.jobs=QueryJobs(self.factory,self.store,max_pending=2)
        self.addCleanup(self.finish)
        self.query=QueryInput(question='Count orders');self.key=uuid.uuid4().hex

    def finish(self):
        self.release.set();self.jobs.pool.shutdown(wait=True);self.jobs.close();self.jobs.cancel_pool.shutdown(wait=True)

    def test_concurrent_replays_bypass_full_queue_and_do_not_duplicate_work(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.jobs.submit(self.query,'owner','stamp',self.key),range(16)))
        self.assertEqual(len({item['id'] for item in results}),1)
        self.assertTrue(self.started.wait(2));self.assertEqual(len(self.calls),1)
        self.assertEqual(sum(bool(item.get('submission_replayed')) for item in results),15)
        self.jobs.max_pending=1
        self.assertEqual(self.jobs.submit(self.query,'owner','stamp',self.key)['id'],results[0]['id'])
        self.assertEqual(self.jobs.metrics.snapshot()['counters']['submitted'],1)

    def test_different_body_or_scope_conflicts_and_different_owner_is_isolated(self):
        first=self.jobs.submit(self.query,'owner','stamp',self.key)
        for query,stamp in ((QueryInput(question='Different'),'stamp'),(self.query,'new-access-stamp')):
            with self.assertRaises(Exception) as error:self.jobs.submit(query,'owner',stamp,self.key)
            self.assertEqual(error.exception.status_code,409)
        second=self.jobs.submit(self.query,'another-owner','stamp',self.key)
        self.assertNotEqual(first['id'],second['id'])

    def test_deleted_and_expired_bindings_do_not_resurrect_queries(self):
        first=self.jobs.submit(self.query,'owner','stamp',self.key)
        self.store.delete_query(first['id']);self.jobs.erase([first['id']])
        with self.assertRaises(Exception) as gone:self.jobs.submit(self.query,'owner','stamp',self.key)
        self.assertEqual(gone.exception.status_code,410)
        with self.store.connect() as connection:
            row=connection.execute('SELECT key_hash,request_hash FROM query_submissions').fetchone()
        self.assertNotIn(self.key,str(row));self.assertNotIn(self.query.question,str(row))

    def test_expiry_rejects_replay_and_retains_tombstone_until_cleanup(self):
        first=self.jobs.submit(self.query,'owner','stamp',self.key)
        metadata=self.store.metadata(first['id'])
        with patch.object(self.store,'clock',return_value=metadata['expires']+1):
            with self.assertRaises(Exception) as error:self.jobs.submit(self.query,'owner','stamp',self.key)
            self.assertEqual(error.exception.status_code,410)

    def test_cancellation_race_discards_late_return_and_same_key_never_restarts_it(self):
        first=self.jobs.submit(self.query,'owner','stamp',self.key)
        self.assertTrue(self.started.wait(2))
        stopped=self.jobs.cancel(first['id']);self.assertTrue(stopped['cancellation_requested'])
        self.release.set();self.jobs.pool.shutdown(wait=True)
        replay=self.jobs.submit(self.query,'owner','stamp',self.key)
        self.assertEqual(replay['id'],first['id']);self.assertEqual(replay['status'],'failed')
        self.assertIsNone(replay['result']);self.assertEqual(len(self.calls),1)

    def test_binding_survives_manager_restart_and_does_not_requeue(self):
        first=self.jobs.submit(self.query,'owner','stamp',self.key)
        self.assertTrue(self.started.wait(2));self.release.set();self.jobs.pool.shutdown(wait=True)
        self.jobs.close()
        def unexpected(*args):self.fail('A replay must not create a workflow')
        second=QueryJobs(unexpected,ReviewStore(self.root/'records.db'))
        try:
            replay=second.submit(self.query,'owner','stamp',self.key)
            self.assertEqual(replay['id'],first['id']);self.assertTrue(replay['submission_replayed'])
            self.assertEqual(replay['status'],'completed');self.assertEqual(second.queue_count(),0)
        finally:second.close()

    def test_atomic_registration_across_store_instances(self):
        identity=QueryJobs.submission_identity(self.query,self.key)
        def register(index):
            job={'id':uuid.uuid4().hex,'owner_id':'owner','source':'sample','access_stamp':'stamp','question':'Count orders','status':'queued'}
            return ReviewStore(self.root/'records.db').register_submission(job,identity)
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(register,range(8)))
        self.assertEqual(sum(item['kind']=='created' for item in results),1)
        self.assertEqual(len({item['id'] for item in results}),1)

    def test_invalid_key_is_rejected_before_admission(self):
        for key in ('short','a'*129,'a'*16+'\n', 'bad key '*4):
            with self.assertRaises(Exception) as error:self.jobs.submit(self.query,'owner','stamp',key)
            self.assertEqual(error.exception.status_code,400)
        self.assertFalse(self.calls)

    def test_initial_durable_snapshot_does_not_retain_unreviewed_question(self):
        first=self.jobs.submit(self.query,'owner','stamp',self.key)
        self.assertTrue(self.started.wait(2))
        saved=self.store.query(first['id'])
        self.assertNotIn(self.query.question,saved['question'])
        self.assertEqual(first['question'],self.query.question)


class MetricsTests(unittest.TestCase):
    def test_provider_usage_failure_categories_and_missing_usage_are_distinct(self):
        class Chat:
            reply=AIMessage(content='',tool_calls=[{'id':'1','name':'IntentOutput','args':{'status':'ready','task':'Count','assumptions':[],'message':''}}],usage_metadata={'input_tokens':12,'output_tokens':8,'total_tokens':20})
            def bind_tools(self,*args,**kwargs):return self
            def invoke(self,*args):
                if isinstance(self.reply,BaseException):raise self.reply
                return self.reply
        chat=Chat();metrics=Metrics()
        model=LangChainModel(config=ModelConfig(model='test',api_key='private-key'),chat=chat,egress_policy=approved_model_policy())
        with observe(metrics),stage('intent'):
            model.generate('Synthetic',{},IntentOutput)
            chat.reply=deepcopy(chat.reply);chat.reply.usage_metadata=None
            model.generate('Synthetic',{},IntentOutput)
            for status in (429,503):
                chat.reply=APIStatusError('DO NOT STORE THIS SECRET',response=Response(status,request=Request('POST','https://api.openai.com/v1')),body=None)
                with self.assertRaises(RuntimeError):model.generate('Synthetic',{},IntentOutput)
        snapshot=metrics.snapshot();usage=snapshot['tokens']['intent']
        self.assertEqual(usage['total_tokens'],20);self.assertEqual(usage['reported_calls'],1)
        self.assertEqual(usage['unknown_usage_calls'],3)
        self.assertEqual({row['outcome'] for row in snapshot['durations']},{'ok','rate_limit','server'})
        self.assertNotIn('SECRET',json.dumps(snapshot));self.assertNotIn('private-key',json.dumps(snapshot))

    def test_metrics_have_bounded_labels_and_cumulative_histograms(self):
        metrics=Metrics()
        for index in range(100):metrics.duration('stage','question-'+str(index),'exception-'+str(index),.2)
        rows=metrics.snapshot()['durations'];self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['name'],'other');self.assertEqual(rows[0]['outcome'],'error')
        self.assertEqual(rows[0]['p95_upper_seconds'],.25)
        self.assertEqual(rows[0]['buckets'][-1]['count'],100)


class OperationsApiTests(unittest.TestCase):
    def test_admin_only_metrics_and_submission_replay_survive_response_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);create_demo(root/'sample.db',expanded=False)
            calls=[]
            class Work:
                def advance(self,*args,**kwargs):
                    calls.append(1)
                    return {'result':{'status':'answered','answer':'Synthetic'}}
            app=create_app(Database(root/'sample.db'),workflow_factory=lambda query:Work(),record_path=root/'records.db')
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/admin/operations').status_code,401)
                sign_in_admin(client)
                key=uuid.uuid4().hex;payload={'question':'Private test question'}
                first=client.post('/api/queries',json=payload,headers={'Idempotency-Key':key})
                # Treat the first response as lost; retry with the same key.
                second=client.post('/api/queries',json=payload,headers={'Idempotency-Key':key})
                self.assertEqual(first.status_code,202);self.assertEqual(second.status_code,202)
                self.assertEqual(first.json()['id'],second.json()['id'])
                app.state.jobs.pool.shutdown(wait=True)
                metrics=client.get('/api/admin/operations').json()
                self.assertEqual(metrics['counters']['submitted'],1);self.assertEqual(metrics['counters']['idempotency_replayed'],1)
                self.assertEqual(metrics['queue']['active_plus_queued'],0)
                self.assertNotIn(payload['question'],json.dumps(metrics));self.assertNotIn(key,json.dumps(metrics))
                member=TestClient(app)
                try:
                    registered=member.post('/api/auth/register',json={'username':'opsmember','password':'Synthetic-password-for-tests-7843!','source':'sample','domain':'sales'})
                    self.assertEqual(registered.status_code,200)
                    self.assertEqual(member.get('/api/admin/operations').status_code,403)
                finally:member.close()


class LoadFailureTests(unittest.TestCase):
    def test_isolated_concurrent_users_outage_timeout_and_cancellation(self):
        for scenario in ('normal','provider_outage','database_timeout','cancellation'):
            with self.subTest(scenario=scenario):
                report=run_load(users=2,requests=4,workers=2,scenario=scenario)
                self.assertTrue(report['passed'],report)
                self.assertTrue(report['checks']['all_replays_same_job'])
                if scenario=='normal':
                    self.assertEqual(report['warehouse_read_attempts'],4)
                    self.assertTrue(report['operations']['tokens'])
