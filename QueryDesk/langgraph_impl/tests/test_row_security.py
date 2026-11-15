import copy
import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import contextmanager,closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from langgraph_impl.access import protected_database
from langgraph_impl.api import create_app
from langgraph_impl.contracts import Candidate
from langgraph_impl.demo import create_demo
from langgraph_impl.pii import PIIPolicy
from langgraph_impl.pii import initialize_key,field_aad
from langgraph_impl.api import QueryJobs,QueryInput,TableConfirmation,SQLDecision
from langgraph_impl.workflow import LangGraphWorkflow
from langgraph_impl.planning import manifest
from langgraph_impl.row_security import RowSecurity,validate
from langgraph_impl.storage import Database,QueryError
from langgraph_impl.warehouse import WarehouseDatabase
from langgraph_impl.tests.auth_helpers import sign_in_admin,PASSWORD
from langgraph_impl.tests.test_nonblocking import DeterministicModel


def fixture_policy():
    return {'version':1,'admin_bypass':True,'attributes':{'groups':{
        'alpha_south':{'tenant_ids':[1],'regions':['South'],'countries':['IN']},
        'alpha_all':{'tenant_ids':[1],'regions':['South','North'],'countries':['IN']},
        'beta_south':{'tenant_ids':[2],'regions':['South'],'countries':['IN']}},'users':{}},'sources':{'custom':{
        'customers':{'mode':'filtered','where':[{'column':'tenant_id','op':'in','attribute':'tenant_ids'},
            {'column':'region','op':'in','attribute':'regions'},{'column':'country','op':'eq','attribute':'countries'}]},
        'orders':{'mode':'filtered','parents':[{'table':'customers','columns':{'tenant_id':'tenant_id','customer_id':'customer_id'}}]},
        'items':{'mode':'filtered','parents':[{'table':'orders','columns':{'tenant_id':'tenant_id','order_id':'order_id'}}]},
        'products':{'mode':'global'}}}}


class RowSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name);self.file=self.path/'row.json'
        self.data=fixture_policy();self.write()
        self.policy=RowSecurity({'ROW_SECURITY_ENABLED':'true','ROW_SECURITY_FILE':str(self.file)})
        self.db_path=self.path/'custom.db'
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript('''
                CREATE TABLE customers(tenant_id INTEGER,customer_id INTEGER,region TEXT,country TEXT,email TEXT,PRIMARY KEY(tenant_id,customer_id));
                CREATE TABLE orders(tenant_id INTEGER,order_id INTEGER,customer_id INTEGER,amount INTEGER,PRIMARY KEY(tenant_id,order_id),FOREIGN KEY(tenant_id,customer_id) REFERENCES customers);
                CREATE TABLE items(tenant_id INTEGER,order_id INTEGER,line_id INTEGER,product_id INTEGER,quantity INTEGER,PRIMARY KEY(tenant_id,order_id,line_id),FOREIGN KEY(tenant_id,order_id) REFERENCES orders);
                CREATE TABLE products(product_id INTEGER PRIMARY KEY,name TEXT);
                CREATE TABLE unmapped(id INTEGER PRIMARY KEY,secret TEXT);
                INSERT INTO customers VALUES(1,1,'South','IN','a@example.test'),(2,1,'South','IN','b@example.test'),(1,2,'North','IN','n@example.test'),(1,3,'South','US','u@example.test'),(1,4,NULL,'IN','null@example.test'),(1,5,'South',NULL,'unknown@example.test');
                INSERT INTO orders VALUES(1,10,1,100),(2,10,1,999),(1,11,2,200),(1,12,3,300),(1,13,NULL,400);
                INSERT INTO items VALUES(1,10,1,1,2),(2,10,1,1,50),(1,11,1,1,3);
                INSERT INTO products VALUES(1,'Widget'),(2,'Other');
                INSERT INTO unmapped VALUES(1,'Never visible');
            ''')
            conn.commit()
        self.db=Database(self.db_path)
        self.pii=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
        self.member={'id':'a'*32,'username':'analyst','role':'member','groups':['business_users','alpha_south']}

    def write(self):self.file.write_text(json.dumps(self.data),encoding='utf-8')
    def view(self,user=None,database=None,allowed=None):
        user=user or self.member;database=database or self.db
        scope=self.policy.scope(database,'custom',user,{'customers':['email']})
        return protected_database(database,allowed or database.allowed,{'customers':['email']},user.get('can_view_pii',user['role']=='admin'),self.pii,'custom',lambda:None,row_scope=scope)

    def test_direct_and_inherited_filters_precede_joins_and_aggregates(self):
        view=self.view()
        self.assertEqual(view.execute('SELECT COUNT(*),SUM(amount) FROM orders',{},['orders'])['rows'],[[1,100]])
        self.assertEqual(view.execute('SELECT SUM(quantity) FROM items',{},['items'])['rows'],[[2]])
        self.assertEqual(view.execute('SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id=c.customer_id',{},['orders','customers'])['rows'],[[1]])
        self.assertEqual(view.execute('SELECT COUNT(*) FROM orders LEFT JOIN items USING(tenant_id,order_id)',{},['orders','items'])['rows'],[[1]])
        self.assertIn('access_note',view.execute('SELECT COUNT(*) FROM orders',{},['orders']))

    def test_ctes_aliases_subqueries_unions_and_or_cannot_bypass(self):
        for sql in ('SELECT COUNT(*) FROM orders WHERE tenant_id=2 OR 1=1',
            'WITH customers AS (SELECT 1 AS anything) SELECT COUNT(*) FROM orders',
            'SELECT COUNT(*) FROM (SELECT * FROM orders) AS __rls_b',
            'SELECT COUNT(*) FROM orders WHERE EXISTS(SELECT 1 FROM customers)',
            'SELECT COUNT(*) FROM orders UNION SELECT COUNT(*) FROM orders'):
            with self.subTest(sql=sql):self.assertEqual(self.view().execute(sql,{},['orders','customers'])['rows'],[[1]])
        self.assertEqual(self.view().execute('SELECT COUNT(*) FROM orders WHERE tenant_id=:tenant',{'tenant':2},['orders'])['rows'],[[0]])
        with self.assertRaises(QueryError):self.view().execute('SELECT COUNT(*) FROM customers WHERE email=:email',{'email':'b@example.test'},['customers'])

    def test_missing_attributes_unknown_tables_and_conflicting_groups_deny_rows(self):
        users=[{'username':'new','role':'member','groups':['business_users']},
            {**self.member,'groups':['alpha_south','beta_south']}]
        for user in users:self.assertEqual(self.view(user).execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[0]])
        self.assertEqual(self.view().execute('SELECT COUNT(*) FROM unmapped',{},['unmapped'])['rows'],[[0]])
        self.assertEqual(self.view().execute('SELECT COUNT(*) FROM products',{},['products'])['rows'],[[2]])
        self.data['attributes']['users'][self.member['id']]={'regions':['North']};self.write();self.policy.refresh()
        self.assertEqual(self.view().execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[0]])

    def test_union_values_in_one_group_and_intersection_across_groups(self):
        broad={**self.member,'groups':['alpha_all']}
        self.assertEqual(self.view(broad).execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[2]])
        narrow={**self.member,'groups':['alpha_all','alpha_south']}
        self.assertEqual(self.view(narrow).execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[1]])

    def test_self_selected_username_cannot_claim_another_accounts_row_grants(self):
        self.data['attributes']['users'][self.member['id']]={'tenant_ids':[1],'regions':['South'],'countries':['IN']}
        self.write();self.policy.refresh()
        other={'id':'b'*32,'username':'analyst','role':'member','groups':[]}
        self.assertEqual(self.view(other).execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[0]])
        self.assertEqual(self.view({**self.member,'groups':[]}).execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[1]])

    def test_composite_relationship_cannot_be_partially_configured(self):
        self.data['sources']['custom']['orders']['parents'][0]['columns']={'customer_id':'customer_id'}
        self.write();self.policy.refresh()
        with self.assertRaises(QueryError):self.view().execute('SELECT COUNT(*) FROM orders',{},['orders'])

    def test_helpers_need_source_permission_but_not_user_table_permission(self):
        view=self.view(allowed={'orders'})
        self.assertEqual(view.catalog()['tables'],['orders'])
        self.assertEqual(view.execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[1]])
        with self.assertRaises(QueryError):view.execute('SELECT * FROM customers',{},['customers'])

    def test_admin_bypass_and_optional_administrator_restrictions(self):
        admin={**self.member,'role':'admin'}
        self.assertEqual(self.view(admin).execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[5]])
        self.data['admin_bypass']=False;self.write();self.policy.refresh()
        self.assertEqual(self.view(admin).execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[1]])

    def test_policy_changes_and_malformed_file_revoke_existing_views(self):
        view=self.view()
        self.data['attributes']['groups']['alpha_south']['regions']=['North'];self.write()
        with self.assertRaises(PermissionError):view.execute('SELECT COUNT(*) FROM orders',{},['orders'])
        self.policy.refresh()
        self.assertEqual(self.view().execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[1]])
        self.file.write_text('{',encoding='utf-8')
        with self.assertRaises(PermissionError):self.view().catalog()

    def test_reject_raw_sql_cycles_unknown_operators_and_pii_predicates(self):
        invalid=[]
        rule=copy.deepcopy(self.data);rule['sql']='1=1';invalid.append(rule)
        rule=copy.deepcopy(self.data);rule['sources']['custom']['customers']['where'][0]['op']='raw';invalid.append(rule)
        rule=copy.deepcopy(self.data);rule['sources']['custom']['customers']['parents']=[{'table':'orders','columns':{'tenant_id':'tenant_id','customer_id':'order_id'}}];invalid.append(rule)
        for rule in invalid:
            with self.assertRaises(ValueError):validate(rule)
        self.data['sources']['custom']['customers']['where'][0]['column']='email';self.write();self.policy.refresh()
        with self.assertRaises(QueryError):self.view().execute('SELECT COUNT(*) FROM customers',{},['customers'])

    def test_manifest_explains_scope_and_uses_metadata_only(self):
        view=self.view()
        with patch.object(view,'open_connection',side_effect=AssertionError('Source row read before approval')):
            result=manifest(view,Candidate.parse({'sql':'SELECT COUNT(*) FROM orders','parameters':[]}),['orders'])
        self.assertTrue(result['row_security']['enabled'])
        self.assertIn('EXISTS',result['row_security']['filters'][0]['sql'])
        self.assertIn(1,result['row_security']['filters'][0]['parameters'].values())
        self.assertNotIn('email',str(result['row_security']))

    def test_unmapped_connected_source_is_denied_until_it_has_its_own_mapping(self):
        scope=self.policy.scope(self.db,'new_warehouse',self.member,{'customers':['email']})
        view=protected_database(self.db,self.db.allowed,{'customers':['email']},False,self.pii,'new_warehouse',lambda:None,row_scope=scope)
        self.assertEqual(view.execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[0]])

    def test_global_only_config_does_not_require_subject_attributes(self):
        self.data={'version':1,'admin_bypass':True,'sources':{'custom':{'products':{'mode':'global'}}}}
        self.write();self.policy.refresh()
        self.assertEqual(self.view().execute('SELECT COUNT(*) FROM products',{},['products'])['rows'],[[2]])
        self.assertEqual(self.view().execute('SELECT COUNT(*) FROM orders',{},['orders'])['rows'],[[0]])

    def test_pii_permission_and_encryption_do_not_bypass_tenant_filters(self):
        initialize_key(self.path/'key')
        config=self.path/'encryption.json'
        config.write_text(json.dumps({'provider':'aes-gcm','key_file':str(self.path/'key'),'sources':{'custom':{'customers':['email']}}}),encoding='utf-8')
        self.pii=PIIPolicy({'PII_ENCRYPTION_ENABLED':'true','PII_CONFIG_FILE':str(config)})
        with closing(sqlite3.connect(self.db_path)) as conn:
            for tenant,customer,email in conn.execute('SELECT tenant_id,customer_id,email FROM customers').fetchall():
                conn.execute('UPDATE customers SET email=? WHERE tenant_id=? AND customer_id=?',
                    (self.pii.cipher.encrypt(email,field_aad('custom','customers','email',[tenant,customer])),tenant,customer))
            conn.commit()
        reader={**self.member,'can_view_pii':True}
        self.assertEqual(self.view(reader).execute('SELECT email FROM customers',{},['customers'])['rows'],[['a@example.test']])
        with self.assertRaises(QueryError):self.view().execute('SELECT email FROM customers',{},['customers'])

    def test_policy_change_during_response_blocks_result_delivery(self):
        from langgraph_impl.persistence import ReviewStore
        owner=self;self.calls=[];self.closed_models=0
        class Model(DeterministicModel):
            def generate(self,instructions,context,output_type):
                result=super().generate(instructions,context,output_type)
                if output_type.__name__=='AnswerOutput':
                    owner.data['attributes']['groups']['alpha_south']['regions']=['North'];owner.write()
                return result
        view=self.view()
        jobs=QueryJobs(lambda *args:LangGraphWorkflow(view,model_factory=lambda role:Model(self)),ReviewStore(self.path/'records.db'))
        self.addCleanup(jobs.close)
        key=jobs.submit(QueryInput(question='How many orders?'),'owner','1')['id']
        for _ in range(400):
            job=jobs.public(key)
            if job['status']=='awaiting_confirmation':
                jobs.confirm(key,TableConfirmation(version=job['confirmation']['version'],tables=['orders']))
            elif job['status']=='awaiting_sql_approval':
                proposal=job['sql_approval']
                jobs.approve_sql(key,SQLDecision(version=proposal['version'],fingerprint=proposal['fingerprint'],decision='approve'))
            elif job['status']=='failed':break
            time.sleep(.005)
        self.assertEqual(job['status'],'failed')
        self.assertIsNone(job['result'])

    def test_postgres_and_mysql_rewriting_filters_every_base_table_on_sqlite_runtime(self):
        engine=create_engine('sqlite:///'+str(self.db_path));self.addCleanup(engine.dispose)
        for kind in ('postgresql','mysql'):
            adapter=WarehouseDatabase.__new__(WarehouseDatabase)
            adapter.kind,adapter.namespace,adapter.allowed=kind,'main',self.db.allowed
            adapter.row_limit,adapter.timeout=100,3
            adapter.schema=self.db.schema
            @contextmanager
            def connection():
                with engine.connect() as conn:yield conn
            adapter.connection=connection
            view=self.view(database=adapter)
            for sql,tables,expected in [
                ('SELECT COUNT(*),SUM(amount) FROM orders',['orders'],[[1,100]]),
                ('SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id=c.customer_id',['orders','customers'],[[1]]),
                ('WITH orders_copy AS (SELECT * FROM orders) SELECT COUNT(*) FROM orders_copy',['orders'],[[1]]),
                ("WITH customers AS (SELECT 2 AS tenant_id,1 AS customer_id) SELECT COUNT(*) FROM orders",['orders'],[[1]]),
                ('SELECT COUNT(*) FROM orders UNION SELECT COUNT(*) FROM orders',['orders'],[[1]]),
                ('SELECT SUM(quantity) FROM items',['items'],[[2]])]:
                with self.subTest(kind=kind,sql=sql):
                    result=view.execute(sql,{},tables)
                    self.assertEqual(result['rows'],expected)
                    self.assertTrue(result['executed_parameters'])
            with self.assertRaises(QueryError):view.execute('SELECT COUNT(*) FROM orders WHERE tenant_id=:__rls_override',{'__rls_override':2},['orders'])


class RowSecurityAPITests(unittest.TestCase):
    def test_additional_connected_sqlite_source_uses_its_own_policy_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            create_demo(path/'sample.db',expanded=False);create_demo(path/'connected.db',expanded=False)
            config=json.loads((Path(__file__).resolve().parents[1]/'row_security.example.json').read_text(encoding='utf-8'))
            config['sources']['connected']=config['sources'].pop('sample')
            config['attributes']['groups']['business_users']={'regions':['South']}
            (path/'rows.json').write_text(json.dumps(config),encoding='utf-8')
            (path/'pii.json').write_text(json.dumps({'provider':'aes-gcm','key_file':str(path/'unused-key'),
                'sources':{'sample':{'customers':['name','phone','mobile','email']},'connected':{'customers':['name','phone','mobile','email']}}}),encoding='utf-8')
            (path/'sources.json').write_text(json.dumps([{'id':'connected','name':'Connected database','kind':'sqlite',
                'connection_env':'ROW_TEST_DB','allowed_tables':['orders','customers']}]),encoding='utf-8')
            self.calls=[];self.closed_models=0
            with patch.dict('os.environ',{'ROW_TEST_DB':str(path/'connected.db')}),patch('langgraph_impl.api.LangChainModel',side_effect=lambda *args,**kwargs:DeterministicModel(self)):
                app=create_app(Database(path/'sample.db'),profiles_file=path/'sources.json',record_path=path/'records.db',security_values={
                    'PII_ENCRYPTION_ENABLED':'false','PII_CONFIG_FILE':str(path/'pii.json'),'ROW_SECURITY_ENABLED':'true','ROW_SECURITY_FILE':str(path/'rows.json')})
                with TestClient(app) as admin,TestClient(app) as member:
                    sign_in_admin(admin)
                    response=member.post('/api/auth/register',json={'username':'connectedanalyst','password':PASSWORD,'source':'connected','domain':'database'})
                    self.assertEqual(response.status_code,200,response.text)
                    member.headers['X-CSRF-Token']=response.json()['csrf_token']
                    response=member.post('/api/queries',json={'source':'connected','question':'How many orders?'})
                    self.assertEqual(response.status_code,202,response.text)
                    key=response.json()['id']
                    for _ in range(500):
                        job=member.get('/api/queries/'+key).json()
                        if job['status']=='awaiting_confirmation':
                            member.post(f'/api/queries/{key}/confirm',json={'version':job['confirmation']['version'],'tables':['orders']})
                        elif job['status']=='awaiting_sql_approval':
                            sql=job['sql_approval']
                            self.assertEqual(sql['source'],'connected')
                            self.assertIn('South',str(sql['row_security']))
                            member.post(f'/api/queries/{key}/approve-sql',json={'version':sql['version'],'fingerprint':sql['fingerprint'],'decision':'approve'})
                        elif job['status'] in {'completed','failed'}:break
                        time.sleep(.005)
                    self.assertEqual(job['status'],'completed',job)
                    self.assertEqual(job['result']['queries'][0]['result']['rows'],[[2]])

    def test_configured_sample_group_hot_reload_and_pending_approval_revocation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);create_demo(path/'sales.db',expanded=False)
            config=json.loads((Path(__file__).resolve().parents[1]/'row_security.example.json').read_text(encoding='utf-8'))
            config['attributes']['groups']['business_users']={'regions':['South']}
            file=path/'row.json';file.write_text(json.dumps(config),encoding='utf-8')
            app=create_app(Database(path/'sales.db'),record_path=path/'records.db',security_values={
                'PII_ENCRYPTION_ENABLED':'false','ROW_SECURITY_ENABLED':'true','ROW_SECURITY_FILE':str(file)})
            self.calls=[];self.closed_models=0
            with TestClient(app) as admin,TestClient(app) as member,patch('langgraph_impl.api.LangChainModel',side_effect=lambda *args,**kwargs:DeterministicModel(self)):
                sign_in_admin(admin)
                registered=member.post('/api/auth/register',json={'username':'analyst','password':PASSWORD,'source':'sample','domain':'sales'}).json()
                member.headers['X-CSRF-Token']=registered['csrf_token']
                self.assertEqual(member.post('/api/queries',json={'question':'How many orders?','attributes':{'regions':['North']}}).status_code,422)
                key=member.post('/api/queries',json={'question':'How many orders?'}).json()['id']
                def wait(status):
                    for _ in range(500):
                        job=member.get('/api/queries/'+key).json()
                        if job.get('status')==status:return job
                        time.sleep(.005)
                    self.fail(str(job))
                proposal=wait('awaiting_confirmation')['confirmation']
                member.post(f'/api/queries/{key}/confirm',json={'version':proposal['version'],'tables':['orders']})
                sql=wait('awaiting_sql_approval')['sql_approval']
                self.assertTrue(sql['row_security']['enabled'])
                self.assertIn('South',str(sql['row_security']))
                member.post(f'/api/queries/{key}/approve-sql',json={'version':sql['version'],'fingerprint':sql['fingerprint'],'decision':'approve'})
                result=wait('completed')['result']
                self.assertEqual(result['queries'][0]['result']['rows'],[[2]])
                self.assertIn('Access scope:',result['answer'])
                key=member.post('/api/queries',json={'question':'How many orders?'}).json()['id']
                wait('awaiting_confirmation')
                epoch=app.state.security.epoch()
                config['attributes']['groups']['business_users']['regions']=['North']
                file.write_text(json.dumps(config),encoding='utf-8')
                status=admin.get('/api/admin/access')
                self.assertEqual(status.status_code,200,status.text)
                self.assertNotEqual(app.state.security.epoch(),epoch)
                self.assertEqual(member.get('/api/queries/'+key).status_code,404)
                self.assertEqual(app.state.jobs.jobs[key]['status'],'failed')
                file.write_text('{',encoding='utf-8')
                self.assertEqual(admin.get('/api/schema').status_code,503)
