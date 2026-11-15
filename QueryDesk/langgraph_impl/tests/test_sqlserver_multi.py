"""Mapped cross-database governance; native dialect plus translated SQLite execution."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing,contextmanager
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock,patch

try:
    import sqlalchemy
    import sqlglot
    from sqlglot import exp
except ImportError:raise unittest.SkipTest('Install application requirements for SQL Server checks')

from langgraph_impl.sqlserver_multi import SQLServerMultiDatabase,validate_mappings
from langgraph_impl.storage import Database,QueryError
from langgraph_impl.sources import SourceRegistry
from langgraph_impl.pii import PIIPolicy
from langgraph_impl.access import protected_database
from langgraph_impl.row_security import RowSecurity
from langgraph_impl.planning import manifest
from langgraph_impl.contracts import Candidate

MAPPINGS={'crm_customers':{'database':'DatabaseA','schema':'dbo','table':'Customers'},
    'sales_orders':{'database':'DatabaseB','schema':'dbo','table':'Orders'},
    'archive_customers':{'database':'DatabaseB','schema':'archive','table':'Customers'}}
RELATIONS=[{'from_table':'sales_orders','to_table':'crm_customers','columns':{'TenantID':'TenantID','CustomerID':'CustomerID'}}]
QUERY='SELECT a.CustomerName, b.OrderTotal FROM DatabaseA.dbo.Customers a INNER JOIN DatabaseB.dbo.Orders b ON a.CustomerID=b.CustomerID AND a.TenantID=b.TenantID'


class MultiDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.path=Path(self.temp.name)
        self.policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})

    def adapter(self):
        db=SQLServerMultiDatabase.__new__(SQLServerMultiDatabase)
        db.kind,db.namespace,db.row_limit,db.timeout='mssql','dbo',100,3
        db.allowed=set(MAPPINGS);db.table_mappings=deepcopy(MAPPINGS);db.relationships=deepcopy(RELATIONS)
        db.tls12_enabled=True
        return db

    def inspector(self):
        inspector=MagicMock()
        inspector.get_table_names.side_effect=lambda schema: ['Customers'] if schema in {'DatabaseA.dbo','DatabaseB.archive'} else ['Orders'] if schema=='DatabaseB.dbo' else []
        def columns(table,schema):
            names=['TenantID','CustomerID','CustomerName','Email','Region'] if table=='Customers' else ['OrderID','TenantID','CustomerID','OrderTotal']
            return [{'name':name,'type':'TEXT' if name in {'CustomerName','Email','Region'} else 'INTEGER','nullable':False} for name in names]
        inspector.get_columns.side_effect=columns
        inspector.get_pk_constraint.side_effect=lambda table,schema:{'constrained_columns':['TenantID','CustomerID'] if table=='Customers' else ['OrderID']}
        return inspector

    def metadata_adapter(self):
        db=self.adapter();inspector=self.inspector()
        @contextmanager
        def connection():yield MagicMock()
        db.connection=connection
        self.addCleanup(patch.stopall)
        patch('sqlalchemy.inspect',return_value=inspector).start()
        return db,inspector

    def view(self,db,allowed=None,pii=None,can_view=True,scope=None):
        return protected_database(db,allowed or db.allowed,pii or {},can_view,self.policy,'reporting',lambda:None,scope)

    def run_sqlite(self,sql,params):
        tree=sqlglot.parse_one(sql,read='tsql')
        for table in tree.find_all(exp.Table):
            if table.catalog:
                self.assertIn(table.catalog,{'DatabaseA','DatabaseB'})
                table.set('db',exp.to_identifier(table.catalog,quoted=True));table.set('catalog',None)
        with closing(sqlite3.connect(':memory:')) as connection:
            connection.execute("ATTACH ':memory:' AS DatabaseA");connection.execute("ATTACH ':memory:' AS DatabaseB")
            connection.execute('CREATE TABLE DatabaseA.Customers(TenantID INTEGER,CustomerID INTEGER,CustomerName TEXT,Email TEXT,Region TEXT)')
            connection.execute('CREATE TABLE DatabaseB.Orders(OrderID INTEGER,TenantID INTEGER,CustomerID INTEGER,OrderTotal INTEGER)')
            connection.executemany('INSERT INTO DatabaseA.Customers VALUES(?,?,?,?,?)',[(1,7,'Mira','private-south','South'),(2,7,'Noor','private-north','North')])
            connection.executemany('INSERT INTO DatabaseB.Orders VALUES(?,?,?,?)',[(1,1,7,100),(2,2,7,900),(3,None,7,700)])
            return connection.execute(tree.sql(dialect='sqlite'),params).fetchall()

    def test_native_example_and_logical_ids_resolve_to_registered_paths(self):
        db=self.adapter()
        sql,refs=db.checked_sql(QUERY,{},['crm_customers','sales_orders'])
        self.assertEqual(refs,{'crm_customers','sales_orders'})
        self.assertIn('[DatabaseA].[dbo].[Customers]',sql);self.assertIn('[DatabaseB].[dbo].[Orders]',sql)
        self.assertEqual(self.run_sqlite(sql,{}),[('Mira',100),('Noor',900)])
        query='SELECT a.CustomerName,b.OrderTotal FROM crm_customers a JOIN sales_orders b ON a.CustomerID=b.CustomerID AND a.TenantID=b.TenantID'
        logical,_=db.checked_sql(query,{},['crm_customers','sales_orders'])
        self.assertEqual(self.run_sqlite(logical,{}),self.run_sqlite(sql,{}))

    def test_binds_and_same_leaf_names_keep_distinct_identities(self):
        db=self.adapter()
        sql,refs=db.checked_sql('SELECT a.CustomerID,b.CustomerID FROM DatabaseA.dbo.Customers a JOIN DatabaseB.archive.Customers b ON a.CustomerID=b.CustomerID WHERE a.CustomerID=:id',{'id':7},['crm_customers','archive_customers'])
        self.assertEqual(refs,{'crm_customers','archive_customers'});self.assertIn('[DatabaseB].[archive].[Customers]',sql);self.assertIn(':id',sql)

    def test_table_selection_options_are_scoped_without_source_reads(self):
        from langgraph_impl.workflow import LangGraphWorkflow
        db=self.adapter();view=self.view(db,allowed={'crm_customers'})
        with patch.object(db,'connection',side_effect=AssertionError('Picker must not query data')):
            proposal=LangGraphWorkflow(view).table_proposal({'selected_tables':['crm_customers'],'reason':'Review CRM'},
                {'tables':['crm_customers'],'workspaces':['crm']})
        self.assertEqual(proposal['table_options'],[{'id':'crm_customers',**MAPPINGS['crm_customers']}])
        self.assertNotIn('DatabaseB',json.dumps(proposal))
        with self.assertRaises(QueryError):view.table_options(['sales_orders'])

    def test_restored_table_review_rebuilds_current_picker_metadata(self):
        from langgraph_impl.workflow import LangGraphWorkflow
        from langgraph_impl.knowledge import KnowledgeBase
        from langgraph_impl.tests.test_workflow import FixtureModel,READY
        db,inspector=self.metadata_adapter();view=self.view(db,allowed={'crm_customers'})
        scripts={'intent':[READY],'schema':[{'tables':['crm_customers'],'reason':'Select customers'}]}
        flow=LangGraphWorkflow(view,model_factory=lambda role:FixtureModel(scripts[role]),knowledge=KnowledgeBase.generic(view.allowed))
        initial=flow.advance('Show customer IDs')
        self.assertEqual(initial['proposal']['table_options'][0]['database'],'DatabaseA')
        checkpoint={'version':1,'limits':{'attempts':flow.max_attempts,'model_calls':flow.max_model_calls},'kind':'tables',
            'state':initial['state'],'proposal':initial['proposal']}
        old=inspector.get_table_names.side_effect
        inspector.get_table_names.side_effect=lambda schema:['Customers'] if schema=='DatabaseC.dbo' else old(schema)
        db.table_mappings['crm_customers']['database']='DatabaseC'
        with patch.object(db,'execute',side_effect=AssertionError('Restore must not execute')):
            restored=flow.recover_review('Show customer IDs',checkpoint)
        self.assertEqual(restored['proposal']['table_options'][0]['database'],'DatabaseC')
        self.assertEqual(restored['state']['budget_calls'],2)

    def test_unmapped_names_unconfirmed_tables_and_linked_servers_are_rejected(self):
        db=self.adapter()
        for query in ('SELECT * FROM DatabaseC.dbo.Customers','SELECT * FROM DatabaseA.private.Customers',
            'SELECT * FROM LinkedServer.DatabaseA.dbo.Customers','SELECT * FROM Customers',
            "SELECT * FROM OPENQUERY(server,'SELECT 1')",'SELECT * FROM DatabaseB.archive.Customers',
            'SELECT * FROM [DatabaseA.dbo.Customers]','DELETE FROM crm_customers',
            'SELECT * FROM crm_customers; SELECT 1','SELECT * FROM crm_customers WITH (NOLOCK)',
            'SELECT * FROM crm_customers FOR UPDATE','SELECT * FROM crm_customers AS x(a,b)',
            'SELECT CustomerID FROM crm_customers WHERE EXISTS(SELECT 1 FROM DatabaseC.dbo.Customers)'):
            with self.subTest(query=query),self.assertRaises(QueryError):db.checked_sql(query,{},['crm_customers'])

    def test_cte_shadowing_and_self_join_do_not_replace_physical_sources(self):
        db=self.adapter()
        for query in ('WITH crm_customers AS (SELECT 999 AS CustomerID) SELECT a.CustomerID FROM DatabaseA.dbo.Customers a',
            'WITH visible AS (SELECT * FROM crm_customers) SELECT CustomerID FROM visible',
            'SELECT a.CustomerID FROM crm_customers a JOIN crm_customers b ON a.CustomerID=b.CustomerID',
            'SELECT CustomerID FROM crm_customers UNION SELECT CustomerID FROM crm_customers'):
            sql,refs=db.checked_sql(query,{},['crm_customers'])
            self.assertEqual(refs,{'crm_customers'});self.assertIn('[DatabaseA].[dbo].[Customers]',sql)
            self.assertTrue(self.run_sqlite(sql,{}))

    def test_qualified_columns_are_normalized_without_broadening_scope(self):
        db=self.adapter()
        sql,_=db.checked_sql('SELECT DatabaseA.dbo.Customers.CustomerName FROM DatabaseA.dbo.Customers',{},['crm_customers'])
        self.assertEqual(self.run_sqlite(sql,{}),[('Mira',),('Noor',)])
        with self.assertRaises(QueryError):db.checked_sql('SELECT DatabaseC.dbo.Customers.CustomerName FROM DatabaseA.dbo.Customers',{},['crm_customers'])

    def test_metadata_discovers_each_database_schema_and_validates_relationships(self):
        db,inspector=self.metadata_adapter()
        result=db.schema(['crm_customers','sales_orders'])
        self.assertEqual(result['tables'][0]['physical_table'],MAPPINGS['crm_customers'])
        self.assertEqual(len(result['tables'][1]['foreign_keys']),2)
        namespaces={call.kwargs['schema'] for call in inspector.get_columns.call_args_list}
        self.assertEqual(namespaces,{'DatabaseA.dbo','DatabaseB.dbo'})
        from langgraph_impl.agents import SchemaAgent
        from langgraph_impl.contracts import Budget
        contexts=[]
        class Selector:
            def generate(inner,instructions,context,output_type):
                contexts.append(context)
                return {'tables':['crm_customers'],'reason':'Registered DatabaseA customer table'}
        SchemaAgent(self.view(db,allowed={'crm_customers'},pii={'crm_customers':['Email']},can_view=False),Selector(),Budget()).select('Use DatabaseA customers',{'status':'ready','task':'Read customer IDs','assumptions':[],'message':''},None)
        metadata=contexts[0]['catalog']['metadata']
        self.assertEqual(metadata[0]['physical_table'],MAPPINGS['crm_customers'])
        self.assertNotIn('DatabaseB',json.dumps(metadata));self.assertNotIn('Email',metadata[0]['columns'])
        db.relationships[0]['columns']['NotAColumn']='CustomerID'
        with self.assertRaises(QueryError):db.schema(['sales_orders'])

    def test_startup_regular_table_checks_and_credentials_stay_private(self):
        inspector=self.inspector()
        with patch('sqlalchemy.create_engine') as engine,patch('sqlalchemy.inspect',return_value=inspector):
            db=SQLServerMultiDatabase('mssql://reader:secret@server/DatabaseA',list(MAPPINGS),MAPPINGS,RELATIONS)
            self.assertEqual(db.allowed,set(MAPPINGS));db.close()
        inspector.get_table_names.return_value=[];inspector.get_table_names.side_effect=None
        with patch('sqlalchemy.create_engine') as engine,patch('sqlalchemy.inspect',return_value=inspector):
            with self.assertRaises(QueryError) as caught:SQLServerMultiDatabase('mssql://reader:secret@server/DatabaseA',list(MAPPINGS),MAPPINGS)
            self.assertNotIn('secret',str(caught.exception));engine.return_value.dispose.assert_called_once()

    def test_pii_is_removed_before_stars_predicates_or_aliases_run(self):
        db,_=self.metadata_adapter();view=self.view(db,pii={'crm_customers':['CustomerName','Email']},can_view=False)
        plan=view.plan('SELECT * FROM crm_customers',{},['crm_customers'])
        self.assertNotIn('[CustomerName]',plan['effective_sql']);self.assertNotIn('[Email]',plan['effective_sql'])
        self.assertEqual(self.run_sqlite(plan['effective_sql'],{}),[(1,7,'South'),(2,7,'North')])
        for query in ('SELECT CustomerName AS harmless FROM crm_customers',"SELECT COUNT(*) FROM crm_customers WHERE Email='private-north'"):
            plan=view.plan(query,{},['crm_customers'])
            with self.assertRaises(sqlite3.OperationalError):self.run_sqlite(plan['effective_sql'],{})

    def row_scope(self,db):
        config={'version':1,'admin_bypass':False,'attributes':{'groups':{'south':{'regions':['South']}}},'sources':{'reporting':{
            'crm_customers':{'mode':'filtered','where':[{'column':'Region','op':'in','attribute':'regions'}]},
            'sales_orders':{'mode':'filtered','parents':[{'table':'crm_customers','columns':{'TenantID':'TenantID','CustomerID':'CustomerID'}}]}}}}
        path=self.path/'rows.json';path.write_text(json.dumps(config),encoding='utf-8')
        rows=RowSecurity({'ROW_SECURITY_ENABLED':'true','ROW_SECURITY_FILE':str(path)})
        return rows.scope(db,'reporting',{'id':'a'*32,'role':'member','groups':['south']},{})

    def test_cross_database_parent_row_policy_and_hidden_helper_stay_enforced(self):
        db,_=self.metadata_adapter();view=self.view(db,allowed={'sales_orders'},scope=self.row_scope(db))
        plan=view.plan('SELECT SUM(OrderTotal) FROM sales_orders',{},['sales_orders'])
        self.assertIn('[DatabaseA].[dbo].[Customers]',plan['effective_sql']);self.assertIn('[DatabaseB].[dbo].[Orders]',plan['effective_sql'])
        self.assertEqual(self.run_sqlite(plan['effective_sql'],plan['effective_parameters']),[(100,)])
        spoof=view.plan("WITH crm_customers AS (SELECT 2 AS TenantID,7 AS CustomerID,'South' AS Region) SELECT SUM(OrderTotal) FROM sales_orders",{},['sales_orders'])
        self.assertEqual(self.run_sqlite(spoof['effective_sql'],spoof['effective_parameters']),[(100,)])
        self.assertEqual(view.catalog()['tables'],['sales_orders'])
        with self.assertRaises(QueryError):view.plan('SELECT * FROM DatabaseA.dbo.Customers',{},['sales_orders'])

    def test_each_join_cte_union_and_reserved_row_bind_is_protected(self):
        db,_=self.metadata_adapter();view=self.view(db,scope=self.row_scope(db))
        for query in ('SELECT COUNT(*) FROM sales_orders','WITH x AS (SELECT * FROM sales_orders) SELECT COUNT(*) FROM x',
            'SELECT COUNT(*) FROM sales_orders a JOIN sales_orders b ON a.OrderID=b.OrderID',
            'SELECT COUNT(*) FROM sales_orders UNION SELECT COUNT(*) FROM sales_orders'):
            plan=view.plan(query,{},['sales_orders'])
            self.assertEqual(self.run_sqlite(plan['effective_sql'],plan['effective_parameters']),[(1,)])
        with self.assertRaises(QueryError):view.plan('SELECT * FROM sales_orders WHERE OrderID=:__rls_fake',{'__rls_fake':1},['sales_orders'])

    def test_permissions_and_physical_mapping_are_bound_to_approval(self):
        db,_=self.metadata_adapter();view=self.view(db);candidate=Candidate.parse({'sql':QUERY,'parameters':[]})
        before=manifest(view,candidate,['crm_customers','sales_orders'])
        db.table_mappings['sales_orders']['database']='DatabaseA'
        # Introspection for the new target must also succeed before review.
        db.schema=lambda tables:{'tables':[{'table':table,'physical_table':db.table_mappings[table],'columns':[{'name':'CustomerID'}],'foreign_keys':[]} for table in tables]}
        # Use logical IDs so changing the physical target changes the prepared SQL.
        logical=Candidate.parse({'sql':'SELECT COUNT(*) FROM sales_orders','parameters':[]})
        first=manifest(view,logical,['sales_orders']);db.table_mappings['sales_orders']['database']='DatabaseB'
        second=manifest(view,logical,['sales_orders']);self.assertNotEqual(first['fingerprint'],second['fingerprint'])
        self.assertTrue(before['adapter_capabilities']['same_instance_cross_database'])
        with self.assertRaises(QueryError):self.view(db,allowed={'sales_orders'}).plan(QUERY,{},['sales_orders'])

    def test_bad_mappings_and_cross_engine_configuration_fail_closed(self):
        for mappings in ({}, {'crm_customers':{'database':'DatabaseA.dbo','schema':'dbo','table':'Customers'}},
            {'crm_customers':{'server':'linked','database':'DatabaseA','schema':'dbo','table':'Customers'}},
            {'CRM':MAPPINGS['crm_customers']}):
            with self.assertRaises(ValueError):validate_mappings(['crm_customers'],mappings)
        duplicate={'a':MAPPINGS['crm_customers'],'b':MAPPINGS['crm_customers']}
        with self.assertRaises(ValueError):validate_mappings(list(duplicate),duplicate)
        path=self.path/'sample.db'
        with closing(sqlite3.connect(path)) as conn:conn.execute('CREATE TABLE orders(id INTEGER)');conn.commit()
        profile={'id':'cross','name':'Cross database','kind':'mssql','connection_env':'TEST_SQLSERVER_URL','allowed_tables':list(MAPPINGS),'table_mappings':MAPPINGS,'relationships':RELATIONS}
        profiles=self.path/'sources.json';profiles.write_text(json.dumps([profile]),encoding='utf-8')
        registry=SourceRegistry(Database(path),profiles,environ={'TEST_SQLSERVER_URL':'mssql://reader:secret@server/DatabaseA'})
        with patch('langgraph_impl.sqlserver_multi.SQLServerMultiDatabase') as constructor:
            constructor.return_value.allowed=set(MAPPINGS);registry.open('cross');self.assertEqual(constructor.call_args.args[2],MAPPINGS)
        self.assertNotIn('secret',str(registry.list()))
        profile['kind']='postgresql';profiles.write_text(json.dumps([profile]),encoding='utf-8')
        with self.assertRaises(ValueError):SourceRegistry(Database(path),profiles,environ={})

    def test_api_requires_both_domain_grants_and_sql_approval_for_cross_database_read(self):
        import os,time
        from fastapi.testclient import TestClient
        from langgraph_impl.api import create_app
        from langgraph_impl.demo import create_demo
        from langgraph_impl.tests.auth_helpers import sign_in_admin,PASSWORD
        db,_=self.metadata_adapter();db.engine=MagicMock()
        create_demo(self.path/'sample.db')
        knowledge=self.path/'knowledge';knowledge.mkdir()
        (knowledge/'catalog.json').write_text(json.dumps({'workspaces':{'crm':{'tables':['crm_customers'],'description':'Customer records','keywords':[]},'sales':{'tables':['sales_orders'],'description':'Order records','keywords':[]}},'metrics':[],'relationship_rules':[],'ambiguous_terms':{},'policies':[]}),encoding='utf-8')
        (knowledge/'examples.json').write_text('[]',encoding='utf-8')
        profile={'id':'reporting','name':'Mapped reporting','kind':'mssql','connection_env':'MSSQL_TEST_URL','allowed_tables':list(MAPPINGS),'table_mappings':MAPPINGS,'relationships':RELATIONS,'knowledge_directory':str(knowledge)}
        profiles=self.path/'profiles.json';profiles.write_text(json.dumps([profile]),encoding='utf-8')
        env=self.path/'.env';env.write_text('LLM_API_KEY=fixture-only-not-a-real-key\nLLM_MODEL=fixture\nLLM_ENDPOINT=https://unused.example.test/v1\n',encoding='utf-8')
        pii=self.path/'pii.json';pii.write_text(json.dumps({'provider':'aes-gcm','key_file':str(self.path/'unused-key'),'sources':{'reporting':{'crm_customers':['CustomerName','Email']}}}),encoding='utf-8')
        executions=[]
        query='SELECT a.CustomerID,b.OrderTotal FROM crm_customers a JOIN sales_orders b ON a.CustomerID=b.CustomerID AND a.TenantID=b.TenantID'
        class Model:
            def close(self):pass
            def generate(inner,instructions,context,output_type):
                kind=output_type.__name__
                if kind=='ColumnSelection':return {'tables':[{'table':t['table'],'columns':[c['name'] for c in t['columns']]} for t in context['schema']['tables']],'reason':'Required keys and amounts'}
                return {'IntentOutput':{'status':'ready','task':'Join customers and orders','assumptions':[],'message':''},
                    'SchemaSelection':{'tables':['crm_customers','sales_orders'],'reason':'Join the two registered databases'},
                    'SQLSubmission':{'sql':query,'parameters':[]},'ReviewOutput':{'approved':True,'feedback':'Registered composite join'},
                    'AnswerOutput':{'answer':'Two customer orders were returned.','reason':'The approved query joined registered customer and order records.'}}[kind]
        def execute(adapter,sql,parameters,tables):
            rendered,refs=adapter.checked_sql(sql,parameters,tables);bound=adapter.bound_parameters(parameters,refs)
            executions.append(rendered)
            return adapter.format_remote_result(['CustomerID','OrderTotal'],self.run_sqlite(rendered,bound),rendered,bound)
        with patch.dict(os.environ,{'NLQ_ENV_FILE':str(env),'MSSQL_TEST_URL':'mssql://reader:secret@server/DatabaseA'}),patch('langgraph_impl.sqlserver_multi.SQLServerMultiDatabase',return_value=db),patch('langgraph_impl.api.LangChainModel',side_effect=lambda *a,**k:Model()),patch.object(SQLServerMultiDatabase,'execute',execute):
            app=create_app(Database(self.path/'sample.db'),profiles_file=profiles,record_path=self.path/'records.db',security_values={'NLQ_ENV_FILE':str(env),'PII_ENCRYPTION_ENABLED':'false','PII_CONFIG_FILE':str(pii),'ROW_SECURITY_ENABLED':'false','AUTH_COOKIE_SECURE':'false'})
            with TestClient(app) as admin:
                sign_in_admin(admin)
                with TestClient(app) as member:
                    registration=member.post('/api/auth/register',json={'username':'crossanalyst','password':PASSWORD,'source':'reporting','domain':'sales'})
                    self.assertEqual(registration.status_code,200,registration.text);account=registration.json()['user'];member.headers['X-CSRF-Token']=registration.json()['csrf_token']
                    schema=member.get('/api/schema?source=reporting').json();self.assertEqual([t['table'] for t in schema['tables']],['sales_orders'])
                    def submit():
                        response=member.post('/api/queries',json={'source':'reporting','question':'Show customer IDs and order totals','workspace':'auto'})
                        self.assertEqual(response.status_code,202,response.text);return response.json()['id']
                    def wait(identifier,status):
                        end=time.monotonic()+10
                        while time.monotonic()<end:
                            job=member.get('/api/queries/'+identifier).json()
                            if job['status']==status:return job
                            if job['status']=='failed' and status!='failed':self.fail(str(job))
                            time.sleep(.01)
                        self.fail('Did not reach '+status+': '+str(job))
                    denied=wait(submit(),'failed')
                    self.assertEqual(denied['result']['status'],'failed');self.assertEqual(executions,[])
                    updated=admin.post('/api/admin/users/'+account['id']+'/access',json={'revision':account['revision'],'grants':[{'source':'reporting','domain':'sales'},{'source':'reporting','domain':'crm'}],'groups':['business_users'],'pii':False,'enabled':True})
                    self.assertEqual(updated.status_code,200,updated.text)
                    login=member.post('/api/auth/login',json={'username':'crossanalyst','password':PASSWORD});member.headers['X-CSRF-Token']=login.json()['csrf_token']
                    identifier=submit();table_review=wait(identifier,'awaiting_confirmation');self.assertEqual(executions,[])
                    details=table_review['confirmation']['table_options']
                    self.assertEqual({table['id'] for table in details},{'crm_customers','sales_orders'})
                    self.assertNotIn('archive_customers',json.dumps(details))
                    confirmed=member.post('/api/queries/'+identifier+'/confirm',json={'version':table_review['confirmation']['version'],'tables':['crm_customers','sales_orders']})
                    self.assertEqual(confirmed.status_code,200,confirmed.text)
                    proposal=wait(identifier,'awaiting_sql_approval')['sql_approval'];self.assertEqual(executions,[])
                    self.assertIn('[DatabaseA]',proposal['sql']);self.assertIn('[DatabaseB]',proposal['sql']);self.assertNotIn('[Email]',proposal['sql'])
                    decision=member.post('/api/queries/'+identifier+'/approve-sql',json={'version':proposal['version'],'fingerprint':proposal['fingerprint'],'decision':'approve'})
                    self.assertEqual(decision.status_code,200,decision.text)
                    result=wait(identifier,'completed')['result'];self.assertEqual(result['status'],'answered',result)
                    self.assertEqual(result['queries'][0]['result']['rows'],[[7,100],[7,900]]);self.assertEqual(len(executions),1)
