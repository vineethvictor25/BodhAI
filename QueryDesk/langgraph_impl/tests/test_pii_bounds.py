import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from langgraph_impl.access import protected_database
from langgraph_impl.contracts import Candidate
from langgraph_impl.pii import PIIPolicy,initialize_key,field_aad
from langgraph_impl.pii_bounds import PIILimits,PIIBudget,PIIBoundError
from langgraph_impl.planning import manifest
from langgraph_impl.storage import Database
from langgraph_impl.workflow import LangGraphWorkflow


class BoundedPIITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        initialize_key(self.path/'key')
        self.config=self.path/'pii.json'
        self.config.write_text(json.dumps({'provider':'aes-gcm','key_file':str(self.path/'key'),'sources':{'vault':{'people':['secret']}}}),encoding='utf-8')
        self.policy=self.make_policy()
        self.file=self.path/'source.db'
        with closing(sqlite3.connect(self.file)) as conn:
            conn.execute('CREATE TABLE people(id INTEGER PRIMARY KEY,secret TEXT)')
            for identifier,name in enumerate(('Alice','Bob','Carol'),1):
                conn.execute('INSERT INTO people VALUES(?,?)',(identifier,self.policy.cipher.encrypt(name,field_aad('vault','people','secret',[identifier]))))
            conn.commit()
        self.base=Database(self.file)

    def make_policy(self,**values):return PIIPolicy({'PII_ENCRYPTION_ENABLED':'true','PII_CONFIG_FILE':str(self.config),**values})
    def view(self,policy=None):return protected_database(self.base,{'people'},{'people':['secret']},True,policy or self.policy,'vault',lambda:None)

    def test_row_limit_stops_before_decrypting_the_excess_row(self):
        policy=self.make_policy(PII_MAX_COPY_ROWS='2')
        with patch.object(policy.cipher,'decrypt',wraps=policy.cipher.decrypt) as decrypt:
            with self.assertRaisesRegex(PIIBoundError,'row limit'):self.view(policy).execute('SELECT secret FROM people',{},['people'])
        self.assertEqual(decrypt.call_count,2)

    def test_input_ciphertext_and_decryption_count_have_separate_bounds(self):
        for values,reason,calls in [({'PII_MAX_INPUT_BYTES':'1'},'input-byte',0),
            ({'PII_MAX_ENVELOPE_BYTES':'20'},'envelope',0),({'PII_MAX_DECRYPT_CELLS':'1'},'decryption-count',1)]:
            policy=self.make_policy(**values)
            with self.subTest(values=values),patch.object(policy.cipher,'decrypt',wraps=policy.cipher.decrypt) as decrypt:
                with self.assertRaisesRegex(PIIBoundError,reason):self.view(policy).execute('SELECT secret FROM people',{},['people'])
                self.assertEqual(decrypt.call_count,calls)

    def test_plaintext_cell_size_is_checked_before_aes_or_json_parsing(self):
        policy=self.make_policy(PII_MAX_CELL_BYTES='4')
        with patch.object(policy.cipher,'decrypt',wraps=policy.cipher.decrypt) as decrypt:
            with self.assertRaisesRegex(PIIBoundError,'plaintext-cell'):self.view(policy).execute('SELECT secret FROM people',{},['people'])
        decrypt.assert_not_called()

    def test_aggregate_plaintext_failure_closes_memory_and_source_connections(self):
        policy=self.make_policy(PII_MAX_COPY_BYTES='8');view=self.view(policy)
        view.plan('SELECT secret FROM people',{},['people'])
        original=sqlite3.connect;targets=[]
        def connect(*args,**kwargs):
            conn=original(*args,**kwargs)
            if args[0]==':memory:':targets.append(conn)
            return conn
        with patch('langgraph_impl.access.sqlite3.connect',side_effect=connect),patch.object(view,'format_result') as result:
            with self.assertRaisesRegex(PIIBoundError,'plaintext-copy'):view.execute('SELECT secret FROM people',{},['people'])
        result.assert_not_called()
        self.assertTrue(targets)
        with self.assertRaises(sqlite3.ProgrammingError):targets[-1].execute('SELECT 1')
        with closing(original(self.file,timeout=1)) as conn:conn.execute('BEGIN EXCLUSIVE');conn.rollback()

    def test_plaintext_local_temporary_storage_stays_in_memory(self):
        view=self.view();sql='SELECT secret FROM people ORDER BY secret'
        view.plan(sql,{},['people']);original=sqlite3.connect;statements=[]
        def connect(*args,**kwargs):
            conn=original(*args,**kwargs)
            if args[0]==':memory:':conn.set_trace_callback(statements.append)
            return conn
        with patch('langgraph_impl.access.sqlite3.connect',side_effect=connect):
            self.assertEqual(view.execute(sql,{},['people'])['rows'],[['Alice'],['Bob'],['Carol']])
        self.assertIn('PRAGMA temp_store=MEMORY',statements)

    def test_elapsed_preparation_budget_prevents_result_after_decryption(self):
        now=[0.0];original=self.policy.cipher.decrypt
        def decrypt(*args,**kwargs):
            value=original(*args,**kwargs);now[0]=100.0;return value
        with patch('langgraph_impl.access.PIIBudget',side_effect=lambda limits:PIIBudget(limits,clock=lambda:now[0])),patch.object(self.policy.cipher,'decrypt',side_effect=decrypt):
            with self.assertRaisesRegex(PIIBoundError,'time limit'):self.view().execute('SELECT secret FROM people',{},['people'])

    def test_wrong_keys_bad_envelopes_and_nonscalar_values_fail_closed(self):
        initialize_key(self.path/'wrong')
        from langgraph_impl.pii import LocalCipher
        with self.assertRaises(ValueError):LocalCipher(self.path/'wrong',True).decrypt_bounded(self.policy.cipher.encrypt('Alice','test'),'test',PIILimits())
        for value in ('qdpii:v1:not base64!',self.policy.cipher.encrypt(['Alice'],'test')):
            with self.assertRaises(ValueError) as caught:self.policy.cipher.decrypt_bounded(value,'test',PIILimits())
            self.assertNotIn('Alice',str(caught.exception))

    def test_limit_configuration_is_finite_positive_and_under_hard_ceilings(self):
        for values in ({'PII_MAX_COPY_ROWS':'0'},{'PII_MAX_COPY_BYTES':'20000001'},
            {'PII_COPY_TIMEOUT_SECONDS':'nan'},{'PII_COPY_TIMEOUT_SECONDS':'inf'},{'PII_MAX_DECRYPT_CELLS':'-1'}):
            with self.subTest(values=values),self.assertRaises(ValueError):self.make_policy(**values)
        with self.assertRaises(ValueError):PIILimits(max_rows=True)

    def test_limits_are_bound_to_approval_and_cache_identity(self):
        candidate=Candidate.parse({'sql':'SELECT secret FROM people','parameters':[]})
        first=manifest(self.view(),candidate,['people'])
        second=manifest(self.view(self.make_policy(PII_MAX_COPY_ROWS='2')),candidate,['people'])
        self.assertNotEqual(first['fingerprint'],second['fingerprint'])
        self.assertEqual(second['pii_limits']['max_rows'],2)
        self.assertNotIn('key',json.dumps(second['pii_limits']))

    def test_bound_failure_is_terminal_without_response_model_or_repair(self):
        class Model:
            def __init__(self):self.kinds=[]
            def generate(inner,instructions,context,output_type):
                kind=output_type.__name__;inner.kinds.append(kind)
                if kind=='ColumnSelection':return {'tables':[{'table':'people','columns':['id','secret']}],'reason':'Requested PII'}
                return {'IntentOutput':{'status':'ready','task':'Read secret','assumptions':[],'message':''},
                    'SchemaSelection':{'tables':['people'],'reason':'Requested table'},'SQLSubmission':{'sql':'SELECT secret FROM people','parameters':[]},
                    'ReviewOutput':{'approved':True,'feedback':'Approved'}}[kind]
        from langgraph_impl.knowledge import KnowledgeBase
        model=Model();policy=self.make_policy(PII_MAX_COPY_ROWS='1')
        result=LangGraphWorkflow(self.view(policy),model_factory=lambda role:model,knowledge=KnowledgeBase.generic({'people'})).ask('Read secret',confirm_sql=lambda proposal:True)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['queries'],[])
        self.assertEqual(result['error_info']['kind'],'pii_bound')
        self.assertNotIn('AnswerOutput',model.kinds);self.assertEqual(model.kinds.count('SQLSubmission'),1)
