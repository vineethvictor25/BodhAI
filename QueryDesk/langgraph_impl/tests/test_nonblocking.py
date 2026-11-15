import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from langgraph_impl.api import QueryJobs,QueryInput,TableConfirmation,SQLDecision
from langgraph_impl.demo import create_demo
from langgraph_impl.persistence import ReviewStore
from langgraph_impl.storage import Database
from langgraph_impl.workflow import LangGraphWorkflow
from langgraph_impl.value_codec import dumps


class DeterministicModel:
    def __init__(self,owner):self.owner=owner
    def close(self):self.owner.closed_models+=1
    def generate(self,instructions,context,output_type):
        kind=output_type.__name__
        self.owner.calls.append(kind)
        if kind=='ColumnSelection':
            return {'tables':[{'table':table['table'],'columns':[]} for table in context['schema']['tables']],'reason':'Use required columns'}
        return {'IntentOutput':{'status':'ready','task':'Count orders','assumptions':[],'message':''},
            'SchemaSelection':{'tables':['orders'],'reason':'Order records'},
            'SQLSubmission':{'sql':'SELECT COUNT(*) FROM orders','parameters':[]},
            'ReviewOutput':{'approved':True,'feedback':'Correct'},
            'AnswerOutput':{'answer':'There are 5 orders.','reason':'Counted all orders.'}}[kind]


class NonBlockingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        create_demo(self.path/'sales.db',expanded=False)
        self.db=Database(self.path/'sales.db')
        self.store=ReviewStore(self.path/'records.db')
        self.calls=[];self.closed_models=0
        self.revoked=set()
        def guard(owner,stamp):
            if owner in self.revoked:raise PermissionError('Revoked')
        def factory(query,owner,stamp):
            return LangGraphWorkflow(self.db,model_factory=lambda role:DeterministicModel(self))
        self.jobs=QueryJobs(factory,self.store,max_pending=1,guard=guard,confirmation_timeout=3)
        self.addCleanup(self.jobs.close)

    def wait(self,key,status):
        end=time.monotonic()+4
        while time.monotonic()<end:
            job=self.jobs.public(key)
            if job['status']==status:return job
            if job['status']=='failed' and status!='failed':self.fail(str(job))
            time.sleep(.005)
        self.fail(f'Never reached {status}: {job}')

    def submit(self,owner='owner'):
        key=self.jobs.submit(QueryInput(question='How many orders?'),owner,'1:1')['id']
        self.wait(key,'awaiting_confirmation')
        return key

    def tables(self,key):
        proposal=self.wait(key,'awaiting_confirmation')['confirmation']
        return self.jobs.confirm(key,TableConfirmation(version=proposal['version'],tables=['orders']))

    def sql(self,key):
        proposal=self.wait(key,'awaiting_sql_approval')['sql_approval']
        return SQLDecision(version=proposal['version'],fingerprint=proposal['fingerprint'],decision='approve')

    def test_one_worker_completes_another_query_while_first_waits(self):
        first=self.submit('first')
        second=self.submit('second')
        self.tables(second)
        self.jobs.approve_sql(second,self.sql(second))
        completed=self.wait(second,'completed')
        self.assertEqual(completed['result']['queries'][0]['result']['rows'],[[5]])
        self.assertEqual(completed['result']['model_calls'],6)
        self.assertEqual(self.jobs.public(first)['status'],'awaiting_confirmation')
        self.assertEqual(self.jobs.pool._max_workers,1)
        self.assertGreaterEqual(self.closed_models,6)

    def test_sql_wait_also_releases_worker_and_holds_plain_artifacts_only(self):
        first=self.submit();self.tables(first)
        self.wait(first,'awaiting_sql_approval')
        saved=dumps(self.jobs.continuations[first])
        self.assertNotIn('api_key',saved)
        self.assertNotIn('Approval(',saved)
        self.assertNotIn('token',saved.lower())
        self.assertEqual(self.jobs.continuations[first]['state']['rows'],None)
        second=self.submit('another')
        self.assertEqual(self.jobs.public(second)['status'],'awaiting_confirmation')
        self.assertNotIn('Event',saved)

    def test_idle_review_expiry_does_not_need_a_workflow_worker(self):
        self.jobs.confirmation_timeout=.08
        key=self.submit()
        job=self.wait(key,'failed')
        self.assertIn('Review expired',job['error'])
        self.assertNotIn(key,self.jobs.continuations)
        self.assertFalse(self.jobs.expirer is threading.current_thread())

    def test_cancellation_is_immediate_and_replay_cannot_resume(self):
        key=self.submit()
        proposal=self.jobs.public(key)['confirmation']
        decision=TableConfirmation(version=proposal['version'],decision='cancel')
        self.assertEqual(self.jobs.confirm(key,decision)['status'],'failed')
        with self.assertRaises(HTTPException):self.jobs.confirm(key,decision)
        self.assertEqual(self.calls,['IntentOutput','SchemaSelection'])

    def test_revocation_and_deletion_drop_or_refuse_continuations(self):
        first=self.submit('revoked');self.tables(first)
        decision=self.sql(first)
        self.revoked.add('revoked')
        self.jobs.approve_sql(first,decision)
        self.wait(first,'failed')
        self.assertNotIn(first,self.jobs.continuations)
        second=self.submit('deleted')
        self.store.delete_query(second);self.jobs.erase([second])
        self.assertNotIn(second,self.jobs.continuations)
        with self.assertRaises(HTTPException):self.jobs.public(second)
        with self.store.connect() as conn:
            self.assertIsNone(conn.execute('SELECT snapshot FROM queries WHERE id=?',(second,)).fetchone())

    def test_restart_requires_restore_and_rejects_old_approval(self):
        key=self.submit();self.tables(key)
        decision=self.sql(key)
        # Simulate loss of the process-local continuation, rather than graceful shutdown.
        with self.jobs.lock:
            self.jobs.continuations.clear()
            self.jobs.jobs.clear()
        self.jobs.close()
        restarted=QueryJobs(self.jobs.factory,self.store,guard=self.jobs.guard)
        try:
            job=restarted.public(key)
            self.assertEqual(job['status'],'recoverable')
            self.assertIsNone(job['sql_approval'])
            with self.assertRaises(HTTPException):restarted.approve_sql(key,decision)
            restored=restarted.restore_review(key)
            self.assertEqual(restored['status'],'awaiting_sql_approval')
            self.assertNotEqual(restored['sql_approval']['version'],decision.version)
            with self.assertRaises(HTTPException):restarted.approve_sql(key,decision)
        finally:restarted.close()

    def test_budget_and_attempt_limits_survive_every_review(self):
        self.jobs.factory=lambda *args:LangGraphWorkflow(self.db,model_factory=lambda role:DeterministicModel(self),max_model_calls=5)
        key=self.submit();self.tables(key)
        self.jobs.approve_sql(key,self.sql(key))
        job=self.wait(key,'failed')
        self.assertEqual(job['result']['model_calls'],5)
        self.assertIn('budget',job['result']['answer'])
        self.assertEqual(self.calls.count('IntentOutput'),1)
        self.assertEqual(self.calls.count('ReviewOutput'),1)

    def test_changed_plan_after_wait_is_rejected_without_execution(self):
        key=self.submit();self.tables(key)
        decision=self.sql(key)
        self.db.row_limit+=1
        with patch.object(self.db,'execute') as execute:
            self.jobs.approve_sql(key,decision)
            job=self.wait(key,'failed')
        self.assertIn('plan changed',job['error'])
        execute.assert_not_called()

    def test_attempt_limit_and_repaired_table_review_survive_segments(self):
        owner=self
        class RetryModel(DeterministicModel):
            def generate(self,instructions,context,output_type):
                if output_type.__name__=='SQLSubmission':
                    owner.sql_attempts+=1
                    if owner.sql_attempts==1:
                        owner.calls.append('SQLSubmission')
                        return {'sql':'SELECT missing FROM orders','parameters':[]}
                return super().generate(instructions,context,output_type)
        for limit in (1,2):
            self.sql_attempts=0
            self.jobs.factory=lambda *args:LangGraphWorkflow(self.db,model_factory=lambda role:RetryModel(self),max_attempts=limit)
            key=self.submit()
            old=self.jobs.public(key)['confirmation']['version']
            self.tables(key)
            if limit==1:
                self.assertEqual(self.wait(key,'failed')['result']['attempts'],1)
            else:
                renewed=self.wait(key,'awaiting_confirmation')['confirmation']['version']
                self.assertNotEqual(old,renewed)
                self.assertEqual(self.jobs.continuations[key]['state']['attempts'],2)
                self.tables(key)
                self.jobs.approve_sql(key,self.sql(key))
                self.assertEqual(self.wait(key,'completed')['result']['attempts'],2)

    def test_sql_rejection_never_executes_and_account_invalidation_cancels_wait(self):
        first=self.submit();self.tables(first)
        decision=self.sql(first)
        with patch.object(self.db,'execute') as execute:
            decision.decision='cancel'
            self.assertEqual(self.jobs.approve_sql(first,decision)['status'],'failed')
            execute.assert_not_called()
        second=self.submit('member')
        self.jobs.cancel_waiting('member')
        self.assertEqual(self.jobs.public(second)['status'],'failed')
        self.assertNotIn(second,self.jobs.continuations)

    def test_resume_replay_and_queue_full_leave_proposal_protected(self):
        key=self.submit()
        first=self.jobs.public(key)['confirmation']
        decision=TableConfirmation(version=first['version'],tables=['orders'])
        # A running segment occupies the sole admitted runnable slot.
        with self.jobs.lock:
            self.jobs.jobs['busy']={'id':'busy','status':'running'}
            with self.assertRaises(HTTPException) as caught:self.jobs.confirm(key,decision)
            self.assertEqual(caught.exception.status_code,429)
            self.assertEqual(self.jobs.jobs[key]['confirmation']['version'],first['version'])
            del self.jobs.jobs['busy']
        self.jobs.confirm(key,decision)
        with self.assertRaises(HTTPException):self.jobs.confirm(key,decision)
        self.wait(key,'awaiting_sql_approval')

    def test_shutdown_cancels_waits_without_thread_leaks(self):
        key=self.submit()
        self.jobs.close()
        self.assertFalse(self.jobs.expirer.is_alive())
        self.assertEqual(self.jobs.continuations,{})
        self.assertEqual(self.store.query(key)['status'],'awaiting_confirmation')
        self.assertEqual(self.store.progress_metadata(key)['phase'],'waiting')

    def test_approval_expiring_during_final_metadata_check_cannot_execute(self):
        from langgraph_impl.planning import manifest
        key=self.submit();self.tables(key)
        decision=self.sql(key)
        proposal=self.jobs.jobs[key]['sql_approval']
        def elapsed_during_planning(*args,**kwargs):
            result=manifest(*args,**kwargs)
            proposal['deadline']=time.time()-1
            return result
        with patch('langgraph_impl.planning.manifest',side_effect=elapsed_during_planning),patch.object(self.db,'execute') as execute:
            self.jobs.approve_sql(key,decision)
            job=self.wait(key,'failed')
        self.assertIn('approval expired before execution',job['error'])
        execute.assert_not_called()
