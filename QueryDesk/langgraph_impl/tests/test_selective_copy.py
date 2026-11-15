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


class SelectiveCopyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name);self.file=self.path/'data.db'
        initialize_key(self.path/'key')
        pii=self.path/'pii.json'
        pii.write_text(json.dumps({'provider':'aes-gcm','key_file':str(self.path/'key'),
            'sources':{'external':{'people':['secret','other_secret'],'unrelated':['note']}}}),encoding='utf-8')
        self.pii=PIIPolicy({'PII_ENCRYPTION_ENABLED':'true','PII_CONFIG_FILE':str(pii)})
        with closing(sqlite3.connect(self.file)) as conn:
            conn.executescript('CREATE TABLE people(id INTEGER PRIMARY KEY,tenant INTEGER,label TEXT COLLATE NOCASE,secret TEXT,other_secret TEXT,payload TEXT); CREATE TABLE helper(helper_id INTEGER PRIMARY KEY,person_id INTEGER,tag TEXT); CREATE TABLE unrelated(note TEXT);')
            for identifier,tenant,label,name in [(1,1,'South','Alice'),(2,2,'South','Bob'),(3,1,'south','Carol'),(5,1,'1','Echo')]:
                encrypted=self.pii.cipher.encrypt(name,field_aad('external','people','secret',[identifier]))
                conn.execute('INSERT INTO people VALUES(?,?,?,?,?,?)',(identifier,tenant,label,encrypted,'qdpii:v1:deliberately-unreadable','x'*3000))
            conn.executescript("INSERT INTO helper VALUES(1,1,'x'),(2,3,'y'); INSERT INTO unrelated VALUES('qdpii:v1:bad');")
            conn.commit()
        rows=self.path/'rows.json'
        rows.write_text(json.dumps({'version':1,'admin_bypass':False,'attributes':{'groups':{'readers':{'tenants':[1]}}},'sources':{'external':{
            'people':{'mode':'filtered','where':[{'column':'tenant','op':'in','attribute':'tenants'}]},
            'helper':{'mode':'filtered','parents':[{'table':'people','columns':{'person_id':'id'}}]},'unrelated':{'mode':'global'}}}}),encoding='utf-8')
        self.base=Database(self.file)
        policy=RowSecurity({'ROW_SECURITY_ENABLED':'true','ROW_SECURITY_FILE':str(rows)})
        mapping={'people':['secret','other_secret'],'unrelated':['note']}
        scope=policy.scope(self.base,'external',{'id':'a'*32,'role':'member','groups':['readers']},mapping)
        self.view=protected_database(self.base,self.base.allowed,mapping,True,self.pii,'external',lambda:None,scope)

    def test_only_requested_pii_columns_and_keys_are_copied_and_decrypted(self):
        sql='SELECT secret FROM people WHERE id=:id'
        proposal=manifest(self.view,Candidate.parse({'sql':sql,'parameters':[{'name':'id','value':1}]}),['people','unrelated'])
        self.assertEqual([step['table'] for step in proposal['copy_scope']],['people'])
        step=proposal['copy_scope'][0]
        self.assertEqual(step['columns'],['id','secret'])
        self.assertEqual(step['decrypt_columns'],['secret'])
        self.assertIn('__copy_',step['source_sql'])
        with patch.object(self.pii.cipher,'decrypt',wraps=self.pii.cipher.decrypt) as decrypt:
            result=self.view.execute(sql,{'id':1},['people','unrelated'])
        self.assertEqual(result['rows'],[['Alice']])
        self.assertEqual(decrypt.call_count,1)

    def test_non_pii_source_filter_preserves_local_affinity_and_binary_collation(self):
        # A naive source predicate would use NOCASE and lose Alice.
        self.assertEqual(self.view.execute("SELECT secret FROM people WHERE label != 'south' ORDER BY id",{},['people'])['rows'],[['Alice'],['Echo']])
        # The local protected table intentionally has no source-column affinity.
        self.assertEqual(self.view.execute('SELECT secret FROM people WHERE label != 1 ORDER BY id',{},['people'])['rows'],[['Alice'],['Carol'],['Echo']])

    def test_pii_filters_and_or_are_retained_locally(self):
        sql="SELECT secret FROM people WHERE id=1 OR secret='Carol' ORDER BY id"
        proposal=manifest(self.view,Candidate.parse({'sql':sql,'parameters':[]}),['people'])
        self.assertNotIn('__copy_',proposal['copy_scope'][0]['source_sql'])
        self.assertEqual(self.view.execute(sql,{},['people'])['rows'],[['Alice'],['Carol']])
        sql="SELECT secret FROM people WHERE id>=1 AND secret='Carol'"
        proposal=manifest(self.view,Candidate.parse({'sql':sql,'parameters':[]}),['people'])
        self.assertIn('__copy_',proposal['copy_scope'][0]['source_sql'])
        self.assertNotIn("'Carol'",proposal['copy_scope'][0]['source_sql'])
        self.assertEqual(self.view.execute(sql,{},['people'])['rows'],[['Carol']])

    def test_outer_join_null_filter_is_never_pushed_across_join(self):
        sql='SELECT p.secret FROM people p LEFT JOIN helper h ON p.id=h.person_id WHERE h.tag IS NULL ORDER BY p.id'
        proposal=manifest(self.view,Candidate.parse({'sql':sql,'parameters':[]}),['people','helper'])
        self.assertTrue(all('__copy_' not in step['source_sql'] for step in proposal['copy_scope']))
        self.assertEqual(self.view.execute(sql,{},['people','helper'])['rows'],[['Echo']])

    def test_row_marker_copy_preserves_join_multiplicity(self):
        sql='SELECT COUNT(p.secret) FROM people p CROSS JOIN helper h'
        proposal=manifest(self.view,Candidate.parse({'sql':sql,'parameters':[]}),['people','helper'])
        helper=next(step for step in proposal['copy_scope'] if step['table']=='helper')
        self.assertTrue(helper['row_marker_only'])
        self.assertIn('NULL AS',helper['source_sql'])
        self.assertEqual(self.view.execute(sql,{},['people','helper'])['rows'],[[6]])

    def test_self_subqueries_keep_the_full_scoped_table(self):
        sql='SELECT p.secret FROM people p WHERE p.id=3 AND EXISTS(SELECT 1 FROM people other WHERE other.id=1)'
        proposal=manifest(self.view,Candidate.parse({'sql':sql,'parameters':[]}),['people'])
        self.assertNotIn('__copy_',proposal['copy_scope'][0]['source_sql'])
        self.assertEqual(self.view.execute(sql,{},['people'])['rows'],[['Carol']])
        sql='SELECT p.secret,(SELECT COUNT(*) FROM people) FROM people p WHERE p.id=3'
        self.assertEqual(self.view.execute(sql,{},['people'])['rows'],[['Carol',3]])

    def test_encrypted_lookup_succeeds_above_old_copy_limit(self):
        with closing(sqlite3.connect(self.file)) as conn:
            conn.executemany('INSERT INTO people VALUES(?,?,?,?,?,?)',((i+100,1,'South','qdpii:v1:bad','qdpii:v1:bad','x'*300) for i in range(100005)))
            conn.commit()
        with patch.object(self.pii.cipher,'decrypt',wraps=self.pii.cipher.decrypt) as decrypt:
            result=self.view.execute('SELECT secret FROM people WHERE id=1',{},['people'])
        self.assertEqual(result['rows'],[['Alice']]);self.assertEqual(decrypt.call_count,1)

    def test_schema_changes_and_private_plan_exposure_are_rejected(self):
        sql='SELECT secret FROM people WHERE id=1'
        public=self.view.plan(sql,{},['people'])
        self.assertNotIn('copy_plan',public)
        self.assertNotIn('schema',str(public['copy_scope'][0].keys()))
        with closing(sqlite3.connect(self.file)) as conn:conn.execute('ALTER TABLE people ADD COLUMN extra TEXT');conn.commit()
        with self.assertRaises(QueryError):self.view.execute(sql,{},['people'])

    def test_pseudo_rowid_prevents_row_filter_pushdown(self):
        sql='SELECT secret FROM people WHERE rowid=1 AND id=3'
        proposal=manifest(self.view,Candidate.parse({'sql':sql,'parameters':[]}),['people'])
        self.assertNotIn('__copy_',proposal['copy_scope'][0]['source_sql'])
        self.assertEqual(self.view.execute(sql,{},['people'])['rows'],[])

    def test_policy_only_dependency_change_invalidates_approved_copy(self):
        with closing(sqlite3.connect(self.file)) as conn:
            conn.executescript('CREATE TABLE acl(tenant INTEGER PRIMARY KEY); INSERT INTO acl VALUES(1),(2);')
        self.base=Database(self.file)
        file=self.path/'rows.json';data=json.loads(file.read_text(encoding='utf-8'))
        data['sources']['external']['acl']={'mode':'global'}
        data['sources']['external']['people']['parents']=[{'table':'acl','columns':{'tenant':'tenant'}}]
        file.write_text(json.dumps(data),encoding='utf-8')
        policy=RowSecurity({'ROW_SECURITY_ENABLED':'true','ROW_SECURITY_FILE':str(file)})
        mapping={'people':['secret','other_secret']}
        scope=policy.scope(self.base,'external',{'id':'a'*32,'role':'member','groups':['readers']},mapping)
        view=protected_database(self.base,{'people'},mapping,True,self.pii,'external',lambda:None,scope)
        view.plan('SELECT secret FROM people WHERE id=1',{},['people'])
        with closing(sqlite3.connect(self.file)) as conn:conn.execute('ALTER TABLE acl ADD COLUMN extra TEXT');conn.commit()
        with patch.object(self.pii.cipher,'decrypt',wraps=self.pii.cipher.decrypt) as decrypt:
            with self.assertRaises(QueryError):view.execute('SELECT secret FROM people WHERE id=1',{},['people'])
        decrypt.assert_not_called()
