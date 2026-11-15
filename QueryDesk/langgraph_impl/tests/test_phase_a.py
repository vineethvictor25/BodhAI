import json
import tempfile
import threading
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime,timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock,patch

from langgraph_impl.persistence import ReviewStore
from langgraph_impl.api import QueryJobs,QueryInput
from langgraph_impl.value_codec import dumps,loads,encode,display
from langgraph_impl.pii import LocalCipher,initialize_key
from langgraph_impl.model_policy import ModelPolicy,EgressDenied
from langgraph_impl.config import ModelConfig
from langgraph_impl.models import LangChainModel
from langgraph_impl.contracts import IntentOutput,Candidate
from langgraph_impl.evaluation import equivalent_rows
from langgraph_impl.tests.auth_helpers import approved_model_policy


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.now=[1000.0]
        self.store=ReviewStore(self.path/'records.db',ttl_seconds=10,clock=lambda:self.now[0])

    def job(self,key='one',owner='owner'):
        job={'id':key,'owner_id':owner,'source':'sample','status':'completed','question':'Private question',
            'result':{'answer':'Private answer','queries':[{'result':{'rows':[[Decimal('12.50')]],'returned_rows':1}}]}}
        self.store.register(job);self.assertTrue(self.store.save_query(job));return job

    def test_expiry_and_feedback_cascade_without_any_new_submissions(self):
        job=self.job()
        self.store.feedback('one','helpful','answer','Private comment')
        self.assertEqual(self.store.query('one')['result']['queries'][0]['result']['rows'],[])
        self.now[0]+=11
        self.assertIsNone(self.store.query('one'))
        self.assertEqual(self.store.reviews(),[])
        self.assertEqual(self.store.maintenance(),1)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM queries').fetchone()[0],0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM feedback').fetchone()[0],0)
        self.assertFalse(self.store.save_query(job))
        self.now[0]+=8*86400;self.store.maintenance()
        self.assertIsNone(self.store.metadata('one'))
        self.assertFalse(self.store.save_query(job))

    def test_expiry_across_restart_and_encrypted_storage(self):
        initialize_key(self.path/'key')
        cipher=LocalCipher(self.path/'key',True)
        store=ReviewStore(self.path/'encrypted.db',cipher=cipher,ttl_seconds=10,clock=lambda:self.now[0],store_rows=True)
        job={'id':'private','owner_id':'owner','result':{'queries':[{'result':{'rows':[[Decimal('12345678901234567890.00100')]]}}]}}
        store.register(job);store.save_query(job)
        with store.connect() as conn:self.assertNotIn('1234567890',conn.execute('SELECT snapshot FROM queries').fetchone()[0])
        self.assertEqual(store.query('private')['result']['queries'][0]['result']['rows'][0][0],Decimal('12345678901234567890.00100'))
        self.now[0]+=11
        restarted=ReviewStore(self.path/'encrypted.db',cipher=cipher,clock=lambda:self.now[0])
        self.assertIsNone(restarted.query('private'))

    def test_owner_delete_and_concurrent_late_writer_cannot_resurrect(self):
        other=self.job('other','another')
        for index in range(8):
            job=self.job(str(index))
            barrier=threading.Barrier(2)
            def write():barrier.wait();return self.store.save_query(job)
            def erase():barrier.wait();self.store.delete_query(job['id'])
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(write),pool.submit(erase)]
                for future in futures:future.result()
            self.assertIsNone(self.store.query(job['id']))
            with self.assertRaises(ValueError):self.store.feedback(job['id'],'helpful','answer','late')
        self.assertTrue(self.store.is_live(other['id']))
        self.assertEqual(self.store.delete_owner('owner'),[])
        self.assertTrue(self.store.is_live(other['id']))

    def test_legacy_is_quarantined_until_explicit_adoption_or_purge(self):
        legacy=self.path/'legacy.db'
        with closing(sqlite3.connect(legacy)) as conn:
            conn.execute('CREATE TABLE queries(id TEXT PRIMARY KEY,snapshot TEXT NOT NULL)')
            conn.executemany('INSERT INTO queries VALUES(?,?)',[(key,json.dumps(payload)) for key,payload in [
                ('old',{'created_at':datetime.fromtimestamp(900,timezone.utc).isoformat(),'owner_id':'owner'}),
                ('bad',{'created_at':'bad'}),('future',{'created_at':datetime.fromtimestamp(2000,timezone.utc).isoformat()})]])
            conn.commit()
        store=ReviewStore(legacy,ttl_seconds=10,clock=lambda:self.now[0])
        self.assertFalse(store.is_live('old'))
        report=store.legacy_inventory()
        self.assertEqual(report['adoptable'],1)
        self.assertEqual(report['invalid_timestamp_or_unreadable'],2)
        self.assertEqual(report['already_expired'],1)
        store.legacy_inventory(apply=True)
        self.assertEqual(store.maintenance(),1)
        store.legacy_inventory(apply=True,purge=True)
        with store.connect() as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM queries').fetchone()[0],0)

    def test_memory_cache_does_not_bypass_expiry_and_deleted_worker_completion(self):
        started,finish=threading.Event(),threading.Event()
        class Workflow:
            def advance(inner,*args,**kwargs):
                started.set();finish.wait(3)
                return {'result':{'status':'answered','answer':'private','queries':[]}}
        jobs=QueryJobs(lambda *args:Workflow(),self.store)
        try:
            job=jobs.submit(QueryInput(question='Count orders'),'owner','1:1')
            self.assertTrue(started.wait(2))
            self.store.delete_query(job['id']);finish.set()
            jobs.pool.shutdown(wait=True)
            with self.assertRaises(Exception):jobs.public(job['id'])
            self.assertIsNone(self.store.query(job['id']))
            self.assertFalse(self.store.save_query({'id':job['id']}))
        finally:finish.set();jobs.close()


    def test_rejected_payload_is_removed_from_durable_snapshot(self):
        for raised in (False,True):
            secret='private@example.com'
            class Workflow:
                def advance(inner,*args,**kwargs):
                    kwargs['on_event']({'stage':'sql','status':'completed','sql':'SELECT :email','parameters':{'email':secret}})
                    if raised:raise EgressDenied('Sensitive content blocked')
                    return {'result':{'status':'failed','answer':secret,'error_info':{'kind':'egress_policy'},'queries':[secret]}}
            jobs=QueryJobs(lambda *args:Workflow(),self.store)
            try:
                job=jobs.submit(QueryInput(question='Find '+secret),'owner','1:1')
                jobs.pool.shutdown(wait=True)
                saved=dumps(self.store.query(job['id']))
                self.assertNotIn(secret,saved)
                self.assertIn('content not retained',saved)
            finally:jobs.close()


class ExactValueTests(unittest.TestCase):
    def test_decimal_large_integer_and_encryption_round_trip(self):
        values=[Decimal('9007199254740993.01000000'),Decimal('-0.00000100'),2**63-1,None,'1.2300']
        self.assertEqual(loads(dumps(values)),values)
        wire=json.loads(dumps(values))
        self.assertEqual(wire[0],{'$qd_type':'decimal','value':'9007199254740993.01000000'})
        self.assertEqual(wire[2]['$qd_type'],'integer')
        self.assertEqual(display(values[0]),'9007199254740993.01000000')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'key';initialize_key(path)
            cipher=LocalCipher(path,True)
            self.assertEqual(cipher.decrypt(cipher.encrypt(values,'test'),'test'),values)

    def test_exact_comparison_and_decimal_parameter_contract(self):
        self.assertFalse(equivalent_rows([[9007199254740993]],[[9007199254740992]]))
        self.assertFalse(equivalent_rows([[Decimal('1.00000001')]],[[Decimal('1.00000002')]]))
        candidate=Candidate.parse({'sql':'SELECT :price','parameters':[{'name':'price','value':{'type':'decimal','value':'12345678901234567890.0100'}}]})
        self.assertIsInstance(dict(candidate.parameters)['price'],Decimal)
        self.assertEqual(Candidate.parse(candidate.payload()),candidate)
        for value in (Decimal('NaN'),Decimal('Infinity'),Decimal('1e99999999')):
            with self.assertRaises(ValueError):encode(value)


class EgressTests(unittest.TestCase):
    def policy(self,**overrides):
        profile={'id':'fixture','endpoint':'https://api.openai.com/v1','models':['test'],'approved':True,'classifications':['internal']}
        profile.update(overrides)
        return ModelPolicy({'version':1,'sources':{'sample':{'classification':'internal'}},'profiles':[profile]})

    def test_blocked_payloads_never_invoke_or_bind_the_provider(self):
        for context in ({'question':'Email customer01@customers.example.test'},{'question':'Phone +1-202-555-0101'},{'question':'password=secret1234'},{'error':'gsk_'+'x'*25}):
            chat=MagicMock()
            model=LangChainModel(config=ModelConfig(model='test',api_key='not-a-real-key'),chat=chat,egress_policy=self.policy())
            with self.assertRaises(EgressDenied):model.generate('role',context,IntentOutput)
            chat.bind_tools.assert_not_called();chat.invoke.assert_not_called()

    def test_endpoint_approval_classification_and_separate_pii_sender_grant(self):
        settings=ModelConfig(model='test',api_key='not-a-real-key')
        for policy,source in ((self.policy(approved=False),'sample'),(self.policy(),'unknown'),(self.policy(endpoint='https://different.example/v1'),'sample')):
            with self.assertRaises(EgressDenied):policy.check(settings,source,{})
        policy=self.policy(allow_detected_pii=True,pii_sender_groups=['group:reviewed_senders'])
        with self.assertRaises(EgressDenied):policy.check(settings,'sample',{'question':'Email customer01@customers.example.test'},groups=('role:admin',))
        policy.check(settings,'sample',{'question':'Email customer01@customers.example.test'},groups=('group:reviewed_senders',))
        with patch.dict('os.environ',{'LANGSMITH_TRACING':'true'}):
            with self.assertRaises(EgressDenied):self.policy().check(settings,'sample',{})

    def test_http_redirects_do_not_forward_payload_to_another_destination(self):
        counts={'destination':0,'approved':0}
        class Destination(BaseHTTPRequestHandler):
            def do_POST(inner):
                counts['destination']+=1;inner.send_response(200);inner.end_headers()
            def log_message(*args):pass
        destination=ThreadingHTTPServer(('127.0.0.1',0),Destination)
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(inner):
                inner.rfile.read(int(inner.headers.get('Content-Length','0')))
                counts['approved']+=1;inner.send_response(302)
                inner.send_header('Location',f'http://127.0.0.1:{destination.server_port}/sink')
                inner.send_header('Content-Length','0');inner.end_headers()
            def log_message(*args):pass
        approved=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
        for server in (destination,approved):
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        endpoint=f'http://127.0.0.1:{approved.server_port}/v1'
        model=LangChainModel(config=ModelConfig(model='test',api_key='fixture-key',base_url=endpoint,timeout=2),egress_policy=approved_model_policy(endpoint))
        try:
            with self.assertRaises(RuntimeError):model.generate('role',{'question':'Count orders'},IntentOutput)
        finally:model.chat.root_client.close()
        self.assertEqual(counts,{'approved':1,'destination':0})
