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
from langgraph_impl.planning import manifest
from langgraph_impl.row_security import RowSecurity
from langgraph_impl.storage import Database,QueryError


class DirectSQLiteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name);self.filename=self.path/'data.db'
        with closing(sqlite3.connect(self.filename)) as conn:
            conn.executescript('CREATE TABLE records(id INTEGER PRIMARY KEY,tenant INTEGER,amount INTEGER,secret TEXT); CREATE INDEX by_tenant ON records(tenant);')
            conn.executemany('INSERT INTO records VALUES(?,?,?,?)',[(i,1 if i%2 else 2,i,'private-'+str(i)) for i in range(1,17)])
            conn.commit()
        self.base=Database(self.filename)
        self.config={'version':1,'admin_bypass':False,'attributes':{'groups':{'reader':{'tenants':[1]}}},
            'sources':{'customer_database':{'records':{'mode':'filtered','where':[{'column':'tenant','op':'in','attribute':'tenants'}]}}}}
        self.row_file=self.path/'rows.json';self.row_file.write_text(json.dumps(self.config),encoding='utf-8')
        self.row_policy=RowSecurity({'ROW_SECURITY_ENABLED':'true','ROW_SECURITY_FILE':str(self.row_file)})
        self.pii=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})

    def view(self,can_view=False):
        scope=self.row_policy.scope(self.base,'customer_database',{'id':'a'*32,'role':'member','groups':['reader']},{'records':['secret']})
        return protected_database(self.base,{'records'},{'records':['secret']},can_view,self.pii,'customer_database',lambda:None,scope)

    def test_large_scoped_aggregation_has_no_copy_ceiling(self):
        with closing(sqlite3.connect(self.filename)) as conn:
            conn.executemany('INSERT INTO records VALUES(?,?,?,?)',((i+100,1,i,'x'*512) for i in range(120001)))
            conn.commit()
        view=self.view()
        with patch.object(view,'open_connection',side_effect=AssertionError('Materialization attempted')):
            result=view.execute('SELECT COUNT(*),SUM(amount) FROM records',{},['records'])
        self.assertEqual(result['rows'],[[120009,sum(range(120001))+sum(range(1,17,2))]])
        self.assertEqual(result['execution_mode'],'direct')

    def test_hidden_pii_and_cte_context_spoof_cannot_escape_projection(self):
        view=self.view()
        for sql in ('SELECT secret FROM main.records','WITH records AS (SELECT secret FROM main.records) SELECT * FROM records',
            'WITH __rls_b AS (SELECT * FROM main.records) SELECT secret FROM __rls_b',
            'SELECT COUNT(*) FROM records WHERE secret IS NOT NULL',
            'SELECT * FROM pragma_table_info(\'records\')','SELECT * FROM sqlite_schema',
            'SELECT * FROM records; SELECT * FROM records','WITH removed AS (DELETE FROM records RETURNING *) SELECT * FROM removed'):
            with self.subTest(sql=sql),self.assertRaises(QueryError):view.execute(sql,{},['records'])
        for sql in ('WITH records AS (SELECT id,tenant FROM main.records) SELECT COUNT(*) FROM records',
            'WITH __rls_b AS (SELECT * FROM main.records) SELECT COUNT(*) FROM __rls_b',
            'SELECT COUNT(*) FROM main.records a JOIN main.records b ON a.id=b.id'):
            self.assertEqual(view.execute(sql,{},['records'])['rows'],[[8]])

    def test_authorized_pii_uses_declared_bounded_path_and_decrypts(self):
        initialize_key(self.path/'key')
        config=self.path/'encryption.json'
        config.write_text(json.dumps({'provider':'aes-gcm','key_file':str(self.path/'key'),
            'sources':{'customer_database':{'records':['secret']}}}),encoding='utf-8')
        self.pii=PIIPolicy({'PII_ENCRYPTION_ENABLED':'true','PII_CONFIG_FILE':str(config)})
        with closing(sqlite3.connect(self.filename)) as conn:
            for identifier,secret in conn.execute('SELECT id,secret FROM records').fetchall():
                conn.execute('UPDATE records SET secret=? WHERE id=?',(self.pii.cipher.encrypt(secret,field_aad('customer_database','records','secret',[identifier])),identifier))
            conn.commit()
        view=self.view(True)
        count=manifest(view,Candidate.parse({'sql':'SELECT COUNT(*) FROM records','parameters':[]}),['records'])
        self.assertEqual(count['execution_mode'],'direct')
        result=view.execute('SELECT COUNT(*) FROM records',{},['records'])
        self.assertEqual(result['rows'],[[8]])
        pii=manifest(view,Candidate.parse({'sql':'SELECT secret FROM records WHERE id=1','parameters':[]}),['records'])
        self.assertEqual(pii['execution_mode'],'materialized')
        self.assertIn('100,000',pii['read_scope'])
        self.assertEqual(view.execute('SELECT secret FROM records WHERE id=1',{},['records'])['rows'],[['private-1']])

    def test_planning_never_reads_source_rows_and_exposes_adapter_contract(self):
        view=self.view()
        original=self.base.open_connection;statements=[]
        def connection():
            conn=original();conn.set_trace_callback(statements.append);return conn
        with patch.object(self.base,'open_connection',connection):
            public_plan=view.plan('SELECT COUNT(*) FROM records',{},['records'])
            proposal=manifest(view,Candidate.parse({'sql':'SELECT COUNT(*) FROM records','parameters':[]}),['records'])
        self.assertNotIn('physical_schema',public_plan)
        self.assertNotIn('secret',json.dumps(public_plan))
        self.assertFalse([sql for sql in statements if sql.startswith('SELECT ')],statements)
        self.assertTrue(proposal['adapter_capabilities']['protected_direct_reads'])
        self.assertIn('source_schema_fingerprint',proposal)
        self.assertIn('"main"."records"',proposal['sql'])
        self.assertTrue(proposal['parameters'])

    def test_schema_change_after_planning_is_rejected(self):
        view=self.view();view.plan('SELECT COUNT(*) FROM records',{},['records'])
        with closing(sqlite3.connect(self.filename)) as conn:conn.execute('ALTER TABLE records ADD COLUMN extra TEXT');conn.commit()
        with self.assertRaises(QueryError):view.execute('SELECT COUNT(*) FROM records',{},['records'])

    def test_limits_function_allowlist_and_reserved_parameters_remain(self):
        view=self.view();view.row_limit=3
        result=view.execute('SELECT id FROM records ORDER BY id',{},['records'])
        self.assertEqual(result['rows'],[[1],[3],[5]]);self.assertTrue(result['truncated'])
        for sql,params in [('SELECT load_extension(\'bad\')',{}),('SELECT id FROM records WHERE tenant=:__rls_any',{'__rls_any':2})]:
            with self.assertRaises(QueryError):view.execute(sql,params,['records'])
        view.timeout=.001
        with self.assertRaisesRegex(QueryError,'interrupted'):view.execute('WITH RECURSIVE loop(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM loop) SELECT SUM(x) FROM loop',{},['records'])

    def test_named_ctes_preserve_sqlite_semantics(self):
        result=self.view().execute('WITH ids(value) AS (SELECT id FROM records) SELECT SUM(value) FROM ids',{},['records'])
        self.assertEqual(result['rows'],[[64]])

    def test_unknown_connectors_fail_closed(self):
        class Unsupported:kind='future_warehouse'
        with self.assertRaises(QueryError):protected_database(Unsupported(),[],{},False,self.pii,'future',lambda:None)
        class Unregistered:kind='postgresql'
        with self.assertRaises(QueryError):protected_database(Unregistered(),[],{},False,self.pii,'future',lambda:None)
