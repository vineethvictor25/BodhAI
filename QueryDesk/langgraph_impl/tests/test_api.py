from langgraph_impl.tests.auth_helpers import sign_in_admin
import tempfile
import time
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from langgraph_impl.api import create_app
except ImportError:
    TestClient=None

from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database


@unittest.skipIf(TestClient is None,"FastAPI is not installed; run setup-ui.ps1 to enable API tests")
class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/"sales.db"
        create_demo(path)
        self.db=Database(path)
        self.questions=[]
        questions=self.questions
        class Workflow:
            def advance(self,question,*,state=None,decision=None,on_event=None):
                if state is None:
                    questions.append(question)
                    return {'waiting':'tables','state':{'phase':'tables'},'proposal':{'tables':['orders'],
                        'available_tables':['orders','customers'],'workspaces':['sales'],'reason':'Count orders'}}
                if state['phase']=='tables':
                    if decision['tables']!=['orders']:raise ValueError('Test query requires orders')
                    on_event({'stage':'schema','status':'completed','tables':['orders']})
                    on_event({'stage':'sql','status':'completed','sql':'SELECT COUNT(*) FROM orders;','parameters':{}})
                    return {'waiting':'sql','state':{'phase':'sql'},'proposal':{'sql':'SELECT COUNT(*) FROM orders;',
                        'parameters':{},'tables':['orders'],'fingerprint':'d'*64}}
                return {'result':{'status':'answered','answer':'There are 25 orders.','reason':'All order records were counted.',
                    'tables':['orders'],'queries':[],'trace':[{'private':'not public'}],'workspaces':['sales']}}
        self.records=Path(self.temp.name)/"reviews.sqlite3"
        self.app=create_app(self.db,workflow_factory=lambda query:Workflow(),record_path=self.records,confirmation_timeout=2)
        self.client=self.enterContext(TestClient(self.app))
        sign_in_admin(self.client)

    def test_metadata_and_health_do_not_expose_credentials(self):
        self.assertEqual(self.client.get("/api/health").json()["service"],"querydesk")
        config=self.client.get("/api/config").json()
        self.assertEqual(config["table_count"],19)
        self.assertNotIn("api_key",str(config))
        self.assertEqual(len(self.client.get("/api/schema").json()["tables"]),19)

    def test_query_returns_reason_and_planned_sources(self):
        response=self.client.post("/api/queries",json={"question":"How many orders?","workspace":"sales"})
        self.assertEqual(response.status_code,202)
        key=response.json()["id"]
        proposal=self.wait_status(key,"awaiting_confirmation")
        self.assertIsNone(proposal["planned_sql"])
        confirmation={"version":proposal["confirmation"]["version"],"tables":["orders"]}
        self.assertEqual(self.client.post(f"/api/queries/{key}/confirm",json={**confirmation,"tables":["private"]}).status_code,422)
        self.assertEqual(self.client.post(f"/api/queries/{key}/confirm",json={**confirmation,"version":"b"*32}).status_code,409)
        self.assertEqual(self.client.post(f"/api/queries/{key}/feedback",json={"rating":"helpful"}).status_code,409)
        self.assertEqual(self.client.post(f"/api/queries/{key}/confirm",json=confirmation).status_code,200)
        sql=self.wait_status(key,'awaiting_sql_approval')['sql_approval']
        self.assertEqual(self.client.post(f'/api/queries/{key}/approve-sql',json={'version':sql['version'],'fingerprint':'e'*64,'decision':'approve'}).status_code,409)
        decision={'version':sql['version'],'fingerprint':sql['fingerprint'],'decision':'approve'}
        self.assertEqual(self.client.post(f'/api/queries/{key}/approve-sql',json=decision).status_code,200)
        self.assertEqual(self.client.post(f'/api/queries/{key}/approve-sql',json=decision).status_code,409)
        for _ in range(100):
            job=self.client.get("/api/queries/"+key).json()
            if job["status"] in {"completed","failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"],"completed")
        self.assertEqual(job["planned_tables"],["orders"])
        self.assertEqual(job["planned_sql"],"SELECT COUNT(*) FROM orders;")
        self.assertEqual(job["result"]["reason"],"All order records were counted.")
        self.assertNotIn("trace",job["result"])
        self.assertEqual(self.questions,["How many orders?"])
        self.assertEqual(self.client.post(f"/api/queries/{key}/confirm",json=confirmation).status_code,409)
        feedback=self.client.post(f"/api/queries/{key}/feedback",json={"rating":"needs_work","category":"sql","comment":"Please verify the date filter."})
        self.assertEqual(feedback.status_code,201)
        self.assertEqual(feedback.json()["review_status"],"pending")
        from langgraph_impl.persistence import ReviewStore
        reopened=ReviewStore(self.records)
        self.assertEqual(reopened.reviews()[0]["query_id"],key)
        self.assertEqual(reopened.query(key)["result"]["answer"],"There are 25 orders.")
        self.assertEqual(reopened.query(key)["confirmations"][0]["tables"],["orders"])
        # Simulate a fresh job manager after restart, preserving completed history and feedback.
        with TestClient(create_app(self.db,workflow_factory=lambda query:None,record_path=self.records)) as restarted:
            sign_in_admin(restarted)
            self.assertEqual(restarted.get(f"/api/queries/{key}").json()["status"],"completed")
            self.assertEqual(restarted.post(f"/api/queries/{key}/feedback",json={"rating":"helpful"}).status_code,201)

    def wait_status(self,key,status,client=None):
        client=client or self.client
        for _ in range(150):
            job=client.get("/api/queries/"+key).json()
            if job["status"]==status:
                return job
            time.sleep(.01)
        self.fail(f"Never reached {status}: {job}")

    def test_cancellation_and_timeout_never_generate_sql(self):
        for cancel in (True,False):
            with self.subTest(cancel=cancel):
                self.app.state.jobs.confirmation_timeout=.05 if not cancel else 2
                key=self.client.post("/api/queries",json={"question":"Count orders"}).json()["id"]
                proposal=self.wait_status(key,"awaiting_confirmation")["confirmation"]
                if cancel:
                    self.client.post(f"/api/queries/{key}/confirm",json={"version":proposal["version"],"decision":"cancel"})
                job=self.wait_status(key,"failed")
                self.assertIsNone(job["planned_sql"])
                self.assertIsNone(job["result"])

    def test_a_second_question_reaches_review_while_first_is_waiting(self):
        first=self.client.post('/api/queries',json={'question':'First count'}).json()['id']
        self.wait_status(first,'awaiting_confirmation')
        second=self.client.post('/api/queries',json={'question':'Second count'}).json()['id']
        self.wait_status(second,'awaiting_confirmation')
        self.assertEqual(self.client.get('/api/queries/'+first).json()['status'],'awaiting_confirmation')

    def test_stop_invalidates_waiting_review_and_requires_csrf(self):
        key=self.client.post('/api/queries',json={'question':'Count orders'}).json()['id']
        review=self.wait_status(key,'awaiting_confirmation')['confirmation']
        csrf=self.client.headers.pop('X-CSRF-Token')
        self.assertEqual(self.client.post(f'/api/queries/{key}/cancel',json={}).status_code,403)
        self.client.headers['X-CSRF-Token']=csrf
        self.assertEqual(self.client.post(f'/api/queries/{key}/cancel',json={}).status_code,200)
        job=self.wait_status(key,'failed');self.assertTrue(job['cancellation_requested'])
        self.assertEqual(self.client.post(f'/api/queries/{key}/confirm',json={'version':review['version'],'tables':['orders']}).status_code,409)
        self.assertIsNone(job['result'])

    def test_running_stop_discards_late_result_and_preserves_queue_bound(self):
        import threading
        started=threading.Event();release=threading.Event()
        class SlowWorkflow:
            def advance(self,*args,**kwargs):
                started.set();release.wait(3)
                return {'result':{'status':'answered','answer':'Must be discarded'}}
        self.app.state.jobs.factory=lambda *args:SlowWorkflow()
        self.addCleanup(release.set)
        key=self.client.post('/api/queries',json={'question':'Slow question'}).json()['id']
        self.assertTrue(started.wait(2))
        response=self.client.post(f'/api/queries/{key}/cancel',json={})
        self.assertTrue(response.json()['cancellation_requested'])
        self.assertEqual(response.json()['status'],'running')
        self.assertEqual(self.app.state.jobs.queue_count(),1)
        release.set();job=self.wait_status(key,'failed')
        self.assertIsNone(job['result']);self.assertNotIn('Must be discarded',str(job))

    def test_per_user_limit_includes_waiting_reviews(self):
        self.app.state.jobs.max_per_user=1
        key=self.client.post('/api/queries',json={'question':'First question'}).json()['id']
        self.wait_status(key,'awaiting_confirmation')
        self.assertEqual(self.client.post('/api/queries',json={'question':'Second question'}).status_code,429)
        self.client.post(f'/api/queries/{key}/cancel',json={})
        response=self.client.post('/api/queries',json={'question':'Second question'})
        self.assertEqual(response.status_code,202)
        self.wait_status(response.json()['id'],'awaiting_confirmation')

    def test_concurrent_users_keep_review_ownership_and_complete_independently(self):
        member=TestClient(self.app)
        self.addCleanup(member.close)
        registered=member.post('/api/auth/register',json={
            'username':'testmember','password':'Member-password-for-local-tests-7843!',
            'source':'sample','domain':'sales'})
        self.assertEqual(registered.status_code,200,registered.text)
        member.headers['X-CSRF-Token']=registered.json()['csrf_token']

        admin_key=self.client.post('/api/queries',json={'question':'Admin order count','workspace':'sales'}).json()['id']
        member_key=member.post('/api/queries',json={'question':'Member order count','workspace':'sales'}).json()['id']
        admin_review=self.wait_status(admin_key,'awaiting_confirmation',self.client)
        member_review=self.wait_status(member_key,'awaiting_confirmation',member)

        self.assertEqual(member.get('/api/queries/'+admin_key).status_code,404)
        self.assertEqual(self.client.get('/api/queries/'+member_key).status_code,200)
        for client,key,review in (
            (self.client,admin_key,admin_review),
            (member,member_key,member_review),
        ):
            confirmation=review['confirmation']
            response=client.post(f'/api/queries/{key}/confirm',json={
                'version':confirmation['version'],'tables':['orders']})
            self.assertEqual(response.status_code,200,response.text)

        admin_sql=self.wait_status(admin_key,'awaiting_sql_approval',self.client)['sql_approval']
        member_sql=self.wait_status(member_key,'awaiting_sql_approval',member)['sql_approval']
        for client,key,proposal in (
            (self.client,admin_key,admin_sql),
            (member,member_key,member_sql),
        ):
            response=client.post(f'/api/queries/{key}/approve-sql',json={
                'version':proposal['version'],'fingerprint':proposal['fingerprint'],'decision':'approve'})
            self.assertEqual(response.status_code,200,response.text)

        for client,key in ((self.client,admin_key),(member,member_key)):
            job=self.wait_status(key,'completed',client)
            self.assertEqual(job['result']['answer'],'There are 25 orders.')

    def test_sql_rejection_timeout_and_missing_csrf_never_complete_query(self):
        for cancel in (True,False):
            key=self.client.post('/api/queries',json={'question':'Count orders'}).json()['id']
            proposal=self.wait_status(key,'awaiting_confirmation')['confirmation']
            self.client.post(f'/api/queries/{key}/confirm',json={'version':proposal['version'],'tables':['orders']})
            sql=self.wait_status(key,'awaiting_sql_approval')['sql_approval']
            decision={'version':sql['version'],'fingerprint':sql['fingerprint'],'decision':'cancel'}
            token=self.client.headers.pop('X-CSRF-Token')
            self.assertEqual(self.client.post(f'/api/queries/{key}/approve-sql',json=decision).status_code,403)
            self.client.headers['X-CSRF-Token']=token
            if cancel:self.assertEqual(self.client.post(f'/api/queries/{key}/approve-sql',json=decision).status_code,200)
            else:
                time.sleep(2.05)
            job=self.wait_status(key,'failed')
            self.assertIsNone(job['result'])

    def test_bad_inputs_and_foreign_origins_are_rejected(self):
        self.assertEqual(self.client.post("/api/queries",json={"question":"  "}).status_code,422)
        self.assertEqual(self.client.post("/api/queries",json={"question":"Hi","workspace":"admin"}).status_code,422)
        self.assertEqual(self.client.post("/api/queries",json={"question":"Hi","model":"not-available"}).status_code,422)
        self.assertEqual(self.client.post("/api/queries",json={"question":"Hi"},headers={"Origin":"https://untrusted.example"}).status_code,403)
        self.assertEqual(self.client.get("/api/queries/invalid").status_code,404)
