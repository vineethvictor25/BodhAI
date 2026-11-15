import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph_impl.access import protected_database
from langgraph_impl.demo import create_demo
from langgraph_impl.pii import PIIPolicy
from langgraph_impl.storage import Database,QueryError
from langgraph_impl.workflow import LangGraphWorkflow
from langgraph_impl.tests.test_workflow import FixtureModel,READY,COUNT


class SQLApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'sales.db';create_demo(path,expanded=False)
        self.db=Database(path)
        self.view=protected_database(self.db,self.db.allowed,{},False,PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'}),'sample',lambda:None)

    def workflow(self,attempts=1):
        scripts={'intent':[READY],'schema':[{'tables':['orders'],'reason':'Count orders'}]*attempts,
            'sql':[COUNT]*attempts,'validation':[{'approved':True,'feedback':'Correct'}]*attempts,
            'response':[{'answer':'There are 5 orders.','reason':'Counted order rows.'}]}
        return LangGraphWorkflow(self.view,model_factory=lambda role:FixtureModel(scripts.get(role,[])))

    def test_rejection_performs_no_materialization_or_execution(self):
        proposals=[]
        with patch.object(self.view,'open_connection',side_effect=AssertionError('Data read before approval')),patch.object(self.view,'execute') as execute:
            result=self.workflow().ask('How many orders?',confirm_sql=lambda proposal:proposals.append(proposal) or False)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(len(proposals),1)
        self.assertEqual(proposals[0]['tables'],['orders'])
        self.assertIn('COUNT(*)',proposals[0]['sql'])
        self.assertEqual(proposals[0]['execution_mode'],'direct')
        self.assertEqual(len(proposals[0]['fingerprint']),64)
        execute.assert_not_called()

    def test_acceptance_precedes_every_data_read_and_executes_real_query(self):
        approved=[]
        original=self.view.execute
        def execute(*args):
            self.assertEqual(len(approved),1)
            return original(*args)
        with patch.object(self.view,'execute',side_effect=execute):
            result=self.workflow().ask('How many orders?',confirm_sql=lambda proposal:approved.append(proposal) or True)
        self.assertEqual(result['status'],'answered',result)
        self.assertEqual(result['queries'][0]['result']['rows'],[[5]])

    def test_changed_limits_invalidate_approved_manifest(self):
        def approve(proposal):
            self.view.row_limit+=1
            return True
        with patch.object(self.view,'execute') as execute:
            result=self.workflow().ask('How many orders?',confirm_sql=approve)
        self.assertEqual(result['status'],'failed')
        self.assertIn('plan changed',result['answer'])
        execute.assert_not_called()

    def test_execution_repair_requires_fresh_human_approval(self):
        proposals=[];original=self.view.execute
        def execute(*args):
            if len(proposals)==1:raise QueryError('Transient execution failure')
            return original(*args)
        with patch.object(self.view,'execute',side_effect=execute):
            result=self.workflow(2).ask('How many orders?',confirm_sql=lambda proposal:proposals.append(proposal) or True)
        self.assertEqual(result['status'],'answered',result)
        self.assertEqual(len(proposals),2)

    def test_direct_execution_reads_only_confirmed_tables_without_copying(self):
        original=self.db.open_connection
        statements=[]
        def connection():
            conn=original();conn.set_trace_callback(statements.append);return conn
        with patch.object(self.db,'open_connection',connection),patch.object(self.view,'open_connection',side_effect=AssertionError('Unexpected copy')):
            result=self.view.execute('SELECT COUNT(*) FROM orders',{},['orders'])
        reads=[sql for sql in statements if sql.startswith('SELECT ')]
        self.assertEqual(len(reads),1,reads)
        self.assertIn('"main"."orders"',reads[0])
        self.assertEqual(result['execution_mode'],'direct')

    def test_remote_planning_uses_local_rewriting_without_source_explain(self):
        from langgraph_impl.warehouse import WarehouseDatabase
        for kind in ('postgresql','mysql'):
            adapter=WarehouseDatabase.__new__(WarehouseDatabase)
            adapter.kind,adapter.namespace,adapter.allowed=kind,'analytics',{'orders'}
            adapter.row_limit,adapter.timeout=100,3
            adapter.security_projection={'orders':['order_id']}
            with patch.object(adapter,'connection',side_effect=AssertionError('Source EXPLAIN before approval')),patch.object(adapter,'schema',return_value={'tables':[{'table':'orders','columns':[{'name':'order_id'}]}]}):
                plan=adapter.plan('SELECT COUNT(*) FROM orders',{},['orders'])
            self.assertTrue(plan['metadata_only'])
            self.assertIn('LIMIT 101',plan['effective_sql'])
            self.assertIn('analytics',plan['effective_sql'])
            self.assertIn('order_id',plan['effective_sql'])
