import json
import sqlite3
import tempfile
import unittest
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langgraph_impl.api import create_app
from langgraph_impl.auth import SecurityStore,AuthError
from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database,QueryError
from langgraph_impl.access import protected_database
from langgraph_impl.pii import PIIPolicy,initialize_key,field_aad,PREFIX
from langgraph_impl.pii_migrate import migrate
from langgraph_impl.persistence import ReviewStore
from langgraph_impl.tests.auth_helpers import sign_in_admin,PASSWORD


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        create_demo(self.path/'sales.db')
        self.db=Database(self.path/'sales.db')
        self.app=create_app(self.db,record_path=self.path/'records.sqlite3',security_values={'PII_ENCRYPTION_ENABLED':'false'})
        self.client=self.enterContext(TestClient(self.app))

    def member(self,username='analyst'):
        client=self.enterContext(TestClient(self.app))
        response=client.post('/api/auth/register',json={'username':username,'password':PASSWORD,'source':'sample','domain':'sales'})
        self.assertEqual(response.status_code,200,response.text)
        client.headers['X-CSRF-Token']=response.json()['csrf_token']
        return client,response.json()['user']

    def test_setup_is_atomic_and_authentication_is_mandatory(self):
        self.assertEqual(self.client.get('/api/config').status_code,401)
        self.assertEqual(self.client.get('/api/schema').status_code,401)
        self.assertEqual(self.client.get('/api/admin/access').status_code,401)
        admin=sign_in_admin(self.client)
        self.assertEqual(admin['role'],'admin')
        self.assertTrue(admin['can_view_pii'])
        self.assertEqual(self.client.post('/api/auth/setup',json={'username':'anotheradmin','password':PASSWORD,'setup_token':'unit-test-setup-secret-that-is-long-enough'}).status_code,409)
        self.assertEqual(self.client.get('/api/schema').status_code,200)
        with self.app.state.security.connect() as conn:
            row=conn.execute('SELECT password_hash FROM users').fetchone()[0]
            self.assertNotIn(PASSWORD,row)
            self.assertTrue(row.startswith('scrypt-v1:'))
        cookie=self.client.cookies.get('qd_session')
        with self.app.state.security.connect() as conn:
            self.assertNotEqual(conn.execute('SELECT token_hash FROM sessions').fetchone()[0],cookie)

    def test_concurrent_bootstrap_has_exactly_one_administrator(self):
        store=self.app.state.security
        def create(index):
            try:return store.create_user('admin'+str(index),PASSWORD,setup=True)['role']
            except AuthError:return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(create,[1,2]))
        self.assertEqual(results.count('admin'),1)
        self.assertEqual(len(store.users()),1)

    def test_first_administrator_requires_the_configured_setup_token(self):
        for token in ('x'*40,'invalid-token-é-which-is-not-ascii-32chars'):
            with self.subTest(token=token):
                response=self.client.post('/api/auth/setup',json={
                    'username':'wrongadmin','password':PASSWORD,'setup_token':token})
                self.assertEqual(response.status_code,403)
        self.assertTrue(self.app.state.security.setup_required())

    def test_first_administrator_setup_fails_closed_without_a_configured_token(self):
        app=create_app(self.db,record_path=self.path/'no-setup-token.sqlite3',security_values={'AUTH_SETUP_TOKEN':''})
        with TestClient(app) as client:
            response=client.post('/api/auth/setup',json={
                'username':'blockedadmin','password':PASSWORD,'setup_token':'x'*40})
        self.assertEqual(response.status_code,503)
        self.assertTrue(app.state.security.setup_required())

    def test_onboarding_one_domain_metadata_pii_and_admin_controls(self):
        sign_in_admin(self.client)
        client,user=self.member()
        self.assertEqual(len(user['direct_grants']),1)
        self.assertFalse(user['can_view_pii'])
        config=client.get('/api/config').json()
        self.assertEqual([d['id'] for d in config['workspaces']],['sales'])
        schema=client.get('/api/schema').json()
        customers=next(t for t in schema['tables'] if t['table']=='customers')
        self.assertEqual({c['name'] for c in customers['columns']},{'customer_id','region'})
        self.assertNotIn('payments',{t['table'] for t in schema['tables']})
        self.assertEqual(client.post('/api/queries',json={'question':'Count payments','workspace':'payments'}).status_code,422)
        self.assertEqual(client.post('/api/admin/groups',json={'id':'x','name':'x','pii':'allow'}).status_code,403)
        self.assertEqual(client.post('/api/sources/sample/domains/suggest',json={}).status_code,403)
        self.assertEqual(client.post('/api/sources/sample/domains',json={'id':'own','name':'Own','description':'Broaden access','tables':['payments'],'keywords':[]}).status_code,403)
        self.assertEqual(client.get('/api/admin/access').status_code,403)
        self.assertEqual(client.post('/api/auth/register',json={'username':'evil','password':PASSWORD,'source':'sample','domain':'sales','pii':True,'role':'admin'}).status_code,422)

    def test_csrf_logout_and_session_revocation(self):
        sign_in_admin(self.client)
        token=self.client.headers.pop('X-CSRF-Token')
        self.assertEqual(self.client.post('/api/auth/logout',json={}).status_code,403)
        self.client.headers['X-CSRF-Token']=token
        self.assertEqual(self.client.post('/api/auth/logout',json={}).status_code,200)
        self.assertEqual(self.client.get('/api/auth/me').status_code,401)

    def test_inheritance_deny_precedence_and_member_session_revocation(self):
        sign_in_admin(self.client)
        client,user=self.member()
        grant={'source':'sample','domain':'payments'}
        response=self.client.post('/api/admin/groups',json={'id':'finance','name':'Finance','pii':'allow','grants':[grant]})
        self.assertEqual(response.status_code,200)
        body={'revision':user['revision'],'grants':user['direct_grants'],'groups':['business_users','finance'],'pii':False,'enabled':True}
        updated=self.client.post(f"/api/admin/users/{user['id']}/access",json=body)
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertTrue(updated.json()['can_view_pii'])
        self.assertEqual(len(updated.json()['grants']),2)
        self.assertEqual(client.get('/api/config').status_code,401)
        session=client.post('/api/auth/login',json={'username':'analyst','password':PASSWORD}).json()
        client.headers['X-CSRF-Token']=session['csrf_token']
        self.assertIn('payments',[d['id'] for d in client.get('/api/config').json()['workspaces']])
        user=updated.json()
        denied=self.client.post(f"/api/admin/users/{user['id']}/access",json={**body,'revision':user['revision'],'groups':['finance','pii_restricted'],'pii':True})
        self.assertFalse(denied.json()['can_view_pii'])
        self.assertEqual(self.client.post('/api/admin/groups',json={'id':'row','name':'Row policy','pii':'inherit','row_filters':[{'region':'South'}]}).status_code,422)

    def test_ownership_and_old_grants_do_not_expose_other_users_queries(self):
        admin=sign_in_admin(self.client)
        client,user=self.member()
        job={'id':'a'*32,'source':'sample','owner_id':admin['id'],'access_stamp':self.app.state.security.stamp(admin),
            'question':'Private question','status':'completed','result':{'tables':['orders'],'answer':'Private answer'},'planned_tables':['orders']}
        self.app.state.records.register(job)
        self.app.state.records.save_query(job)
        self.assertEqual(client.get('/api/queries/'+job['id']).status_code,404)
        self.assertEqual(client.post('/api/queries/'+job['id']+'/feedback',json={'rating':'helpful'}).status_code,404)
        self.assertEqual(client.post('/api/queries/'+job['id']+'/confirm',json={'version':'b'*32,'tables':['orders']}).status_code,404)
        self.assertEqual(client.post('/api/queries/'+job['id']+'/approve-sql',json={'version':'b'*32,'fingerprint':'c'*64,'decision':'approve'}).status_code,404)
        self.assertEqual(self.client.get('/api/queries/'+job['id']).status_code,200)
        job.update(id='c'*32,owner_id=user['id'],access_stamp=self.app.state.security.stamp(user))
        self.app.state.records.register(job)
        self.app.state.records.save_query(job)
        self.assertEqual(client.get('/api/queries/'+job['id']).status_code,200)
        self.app.state.security.invalidate()
        self.assertEqual(client.get('/api/queries/'+job['id']).status_code,404)

    def test_history_delete_is_owner_scoped_and_csrf_protected(self):
        admin=sign_in_admin(self.client)
        client,user=self.member()
        records=self.app.state.records
        for identifier,owner in [('a'*32,admin),('b'*32,user)]:
            job={'id':identifier,'source':'sample','owner_id':owner['id'],'access_stamp':self.app.state.security.stamp(owner),'status':'completed','question':'Count orders'}
            records.register(job);records.save_query(job)
        self.assertEqual(client.post('/api/queries/'+'a'*32+'/delete',json={}).status_code,404)
        self.assertEqual(client.get('/api/admin/history-retention').status_code,403)
        self.assertEqual(self.client.get('/api/admin/history-retention').status_code,200)
        token=client.headers.pop('X-CSRF-Token')
        self.assertEqual(client.post('/api/history/clear',json={}).status_code,403)
        client.headers['X-CSRF-Token']=token
        self.assertEqual(client.post('/api/history/clear',json={}).json()['deleted_count'],1)
        self.assertIsNone(records.query('b'*32))
        self.assertIsNotNone(records.query('a'*32))
        self.assertEqual(client.get('/api/queries/'+'b'*32).status_code,404)
        self.assertEqual(self.client.post('/api/queries/'+'a'*32+'/delete',json={}).status_code,200)
        self.assertIsNone(records.query('a'*32))

    def test_login_throttle_and_secret_free_validation(self):
        sign_in_admin(self.client)
        for _ in range(8):
            response=self.client.post('/api/auth/login',json={'username':'unknown','password':PASSWORD})
            self.assertEqual(response.status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json={'username':'unknown','password':PASSWORD}).status_code,429)
        response=self.client.post('/api/auth/login',json={'username':'unknown','password':'secret-short'})
        self.assertNotIn('secret-short',response.text)

    def test_real_graph_uses_pii_safe_views_for_members_and_restores_admin_tokens_locally(self):
        sign_in_admin(self.client)
        client,_=self.member()
        response_contexts=[]
        class Model:
            def generate(inner,instructions,context,output_type):
                kind=output_type.__name__
                if kind=='ColumnSelection':
                    return {'tables':[{'table':t['table'],'columns':[c['name'] for c in t['columns']]} for t in context['schema']['tables']],'reason':'Accessible fields'}
                if kind=='AnswerOutput':
                    response_contexts.append(context)
                    columns=context['result']['columns']
                    name=context['result']['rows'][0][columns.index('name')] if 'name' in columns else 'PII unavailable'
                    return {'answer':'First customer: '+str(name),'reason':'Only executed accessible fields were used.'}
                return {'IntentOutput':{'status':'ready','task':'Show customers','assumptions':[],'message':''},
                    'SchemaSelection':{'tables':['customers'],'reason':'Customer records'},
                    'SQLSubmission':{'sql':'SELECT * FROM customers ORDER BY customer_id','parameters':[]},
                    'ReviewOutput':{'approved':True,'feedback':'Accessible customer fields'}}[kind]
        def run(actor):
            response=actor.post('/api/queries',json={'question':'Show the accessible customers','workspace':'sales'})
            self.assertEqual(response.status_code,202,response.text)
            key=response.json()['id']
            confirmed=set()
            for _ in range(300):
                job=actor.get('/api/queries/'+key).json()
                if job['status']=='awaiting_confirmation':
                    version=job['confirmation']['version']
                    if version not in confirmed:
                        confirmed.add(version)
                        actor.post('/api/queries/'+key+'/confirm',json={'version':version,'tables':['customers']})
                if job['status']=='awaiting_sql_approval':
                    proposal=job['sql_approval']
                    actor.post('/api/queries/'+key+'/approve-sql',json={'version':proposal['version'],'fingerprint':proposal['fingerprint'],'decision':'approve'})
                if job['status'] in {'completed','failed'}:return job
                time.sleep(.01)
            self.fail('Query did not finish')
        with patch('langgraph_impl.api.LangChainModel',return_value=Model()):
            member=run(client)
            self.assertEqual(member['result']['status'],'answered',member)
            self.assertNotIn('customer01@',json.dumps(member))
            self.assertEqual(member['result']['queries'][0]['result']['columns'],['customer_id','region'])
            admin=run(self.client)
            self.assertEqual(admin['result']['status'],'answered',admin)
            self.assertIn('Mira',admin['result']['answer'])
            self.assertNotIn('Mira',json.dumps(response_contexts))


class PIIProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        create_demo(self.path/'sales.db')
        self.db=Database(self.path/'sales.db')
        initialize_key(self.path/'key')
        self.config=self.path/'encryption.json'
        self.config.write_text(json.dumps({'provider':'aes-gcm','key_file':str(self.path/'key'),'sources':{'sample':{'customers':['name','phone','mobile','email'],'customer_profiles':['email']}}}))
        self.policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'true','PII_CONFIG_FILE':str(self.config)})

    def view(self,can_view):
        return protected_database(self.db,self.db.allowed,self.policy.columns['sample'],can_view,self.policy,'sample',lambda:None)

    def test_encryption_migration_roundtrip_and_row_bound_authentication(self):
        report=migrate(self.db.filename,'sample',self.policy)
        self.assertGreater(report['changed_values'],40)
        with closing(sqlite3.connect(self.db.filename)) as conn:
            raw=conn.execute('SELECT email FROM customers WHERE customer_id=1').fetchone()[0]
        self.assertTrue(raw.startswith(PREFIX))
        self.assertNotIn('customer01',raw)
        with self.assertRaises(ValueError):
            self.policy.cipher.decrypt(raw,field_aad('sample','customers','email',[2]))
        result=self.view(True).execute('SELECT phone,mobile,email FROM customers WHERE customer_id=:id',{'id':1},['customers'])
        self.assertEqual(result['rows'][0],['+1-202-555-0101','+1-202-555-0151','customer01@customers.example.test'])
        migrate(self.db.filename,'sample',self.policy,decrypt=True,backup=self.path/'second-backup.enc')
        self.assertEqual(self.db.execute('SELECT name FROM customers WHERE customer_id=1',{},['customers'])['rows'],[['Mira']])
        self.assertNotIn('customer01@customers.example.test',Path(report['encrypted_backup']).read_text())

    def test_denied_columns_are_removed_before_sql_including_aliases_and_filters(self):
        for encrypted in (False,True):
            if encrypted:migrate(self.db.filename,'sample',self.policy)
            view=self.view(False)
            rows=view.execute('SELECT * FROM customers',{},['customers'])
            self.assertEqual(rows['columns'],['customer_id','region'])
            self.assertEqual(len(rows['rows']),12)
            for sql in ('SELECT name AS harmless FROM customers','SELECT lower(email) FROM customers',"SELECT COUNT(*) FROM customers WHERE phone LIKE '%0101'",'WITH c AS (SELECT * FROM customers) SELECT mobile FROM c'):
                with self.subTest(sql=sql),self.assertRaises(QueryError):
                    view.execute(sql,{},['customers'])
            with self.assertRaises(QueryError):
                view.execute('SELECT * FROM payments',{},['customers'])

    def test_authorized_result_strings_are_tokenized_before_response_generation(self):
        view=self.view(True)
        result=view.execute('SELECT name,email FROM customers WHERE customer_id=1',{},['customers'])
        model,restore=view.model_rows(result)
        self.assertNotIn('Mira',json.dumps(model))
        self.assertNotIn('customer01',json.dumps(model))
        token=model['rows'][0][0]
        self.assertEqual(restore('Customer: '+token),'Customer: Mira')

    def test_encrypted_application_records_and_wrong_key_fail_closed(self):
        store=ReviewStore(self.path/'records.sqlite3',cipher=self.policy.cipher)
        store.register({'id':'a','owner_id':'owner'})
        store.save_query({'id':'a','answer':'Mira','owner_id':'owner'})
        feedback=store.feedback('a','helpful','answer','Email customer01@customers.example.test')
        with store.connect() as conn:
            self.assertNotIn('Mira',conn.execute('SELECT snapshot FROM queries').fetchone()[0])
            self.assertNotIn('customer01',conn.execute('SELECT comment FROM feedback').fetchone()[0])
        self.assertEqual(store.query('a')['answer'],'Mira')
        self.assertIn('customer01',store.reviews()[0]['comment'])
        initialize_key(self.path/'wrong-key')
        from langgraph_impl.pii import LocalCipher
        wrong=ReviewStore(self.path/'records.sqlite3',cipher=LocalCipher(self.path/'wrong-key'))
        with self.assertRaises(ValueError):wrong.query('a')
        with self.assertRaises(FileExistsError):initialize_key(self.path/'key')

    def test_revoked_policy_blocks_queries_and_unsupported_providers_do_not_fallback(self):
        view=self.view(False)
        def denied():raise PermissionError('Revoked')
        view.check=denied
        with self.assertRaises(PermissionError):view.execute('SELECT COUNT(*) FROM customers',{},['customers'])
        self.config.write_text(json.dumps({'provider':'unknown','sources':{}}))
        with self.assertRaises(ValueError):PIIPolicy({'PII_CONFIG_FILE':str(self.config)})
