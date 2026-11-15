import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
from decimal import Decimal

import sqlglot
from sqlglot import exp
try:
    from google.cloud import bigquery
except ImportError:
    raise unittest.SkipTest('Install requirements-bigquery.txt for Google cloud checks')
from langgraph_impl.bigquery_source import BigQueryDatabase
from langgraph_impl.sources import SourceRegistry
from langgraph_impl.storage import Database,QueryError
from langgraph_impl.pii import PIIPolicy
from langgraph_impl.access import protected_database
from langgraph_impl.planning import manifest
from langgraph_impl.contracts import Candidate
from langgraph_impl.tests import test_warehouse_connectors as connector_tests


class CloudConnectorTests(unittest.TestCase):
    address='bigquery://demo-project/analytics?location=US&maximum_bytes_billed=1000000000'

    def table(self):
        result=bigquery.Table('demo-project.analytics.orders',schema=[bigquery.SchemaField('id','INT64',mode='REQUIRED'),bigquery.SchemaField('region','STRING'),bigquery.SchemaField('amount','NUMERIC'),bigquery.SchemaField('email','STRING')])
        result._properties['type']='TABLE'
        return result

    def source(self):
        from google.auth.credentials import AnonymousCredentials
        client=MagicMock();client.get_table.return_value=self.table()
        with patch('google.auth.default',return_value=(AnonymousCredentials(),'demo-project')),patch('google.cloud.bigquery.Client',return_value=client):db=BigQueryDatabase(self.address,['orders'],'analytics',timeout=30)
        self.addCleanup(db.close)
        return db,client

    def test_bigquery_uses_adc_metadata_only_and_explicit_dataset(self):
        db,client=self.source()
        client.query.assert_not_called()
        self.assertEqual(db.catalog()['project'],'demo-project')
        query,refs=db.checked_sql('SELECT amount FROM orders WHERE id=@identifier',{'identifier':7},['orders'])
        self.assertEqual(refs,{'orders'});self.assertIn('demo-project.analytics.orders',query);self.assertIn('@identifier',query)
        for sql in ('SELECT * FROM `other-project.analytics.orders`','SELECT * FROM `demo-project.private.orders`','SELECT * FROM EXTERNAL_QUERY(:connection,:sql)','EXPORT DATA OPTIONS(uri="gs://bucket/file") AS SELECT * FROM orders'):
            with self.subTest(sql=sql),self.assertRaises(QueryError):db.checked_sql(sql,{},['orders'])

    def test_bigquery_parameters_cost_limit_timeout_and_exact_results(self):
        db,client=self.source();job=client.query.return_value
        class Results(list):schema=[bigquery.SchemaField('amount','NUMERIC')]
        job.result.return_value=Results([(Decimal('12345678901234567890.00100'),)])
        result=db.execute('SELECT amount FROM orders WHERE id=:id',{'id':1},['orders'])
        self.assertEqual(result['rows'],[[Decimal('12345678901234567890.00100')]])
        config=client.query.call_args.kwargs['job_config']
        self.assertEqual(config.maximum_bytes_billed,1000000000)
        self.assertEqual(int(config.job_timeout_ms),30000);self.assertFalse(config.use_legacy_sql)
        self.assertEqual(config.default_dataset.dataset_id,'analytics')
        self.assertEqual(config.query_parameters[0].name,'id')
        self.assertEqual(job.result.call_args.kwargs['max_results'],101)
        self.assertIsNone(client.query.call_args.kwargs['job_retry'])

    def test_bigquery_failure_cancels_and_redacts_provider_details(self):
        db,client=self.source();job=client.query.return_value
        job.result.side_effect=RuntimeError('sensitive details')
        with self.assertRaises(QueryError) as caught:db.execute('SELECT * FROM orders',{},['orders'])
        self.assertNotIn('sensitive details',str(caught.exception));job.cancel.assert_called_once()

    def test_bigquery_approval_binds_cost_limit_and_projection(self):
        db,client=self.source();policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
        view=protected_database(db,{'orders'},{'orders':['email']},False,policy,'gcp',lambda:None)
        candidate=Candidate.parse({'sql':'SELECT * FROM orders','parameters':[]})
        before=manifest(view,candidate,['orders'])
        self.assertNotIn('email',before['sql']);self.assertIn('maximum_bytes_billed',before['resource_limits'])
        client.query.assert_not_called()
        db.maximum_bytes_billed=1
        self.assertNotEqual(before['fingerprint'],manifest(view,candidate,['orders'])['fingerprint'])

    def test_bigquery_rejects_unreviewed_urls_and_nonregular_tables(self):
        for address in (self.address+'&destination=private.table',self.address.replace('1000000000','0'),self.address.replace('bigquery://','bigquery://reader:secret@')):
            with patch('google.cloud.bigquery.Client') as client,self.assertRaises(QueryError):BigQueryDatabase(address,['orders'],'analytics')
            client.assert_not_called()
        for kind in ('VIEW','EXTERNAL'):
            table=self.table();table._properties['type']=kind
            client=MagicMock();client.get_table.return_value=table
            from google.auth.credentials import AnonymousCredentials
            with patch('google.auth.default',return_value=(AnonymousCredentials(),'demo-project')),patch('google.cloud.bigquery.Client',return_value=client),self.assertRaises(QueryError):BigQueryDatabase(self.address,['orders'],'analytics')
            client.close.assert_called_once()

    def test_registry_dispatches_cloud_sources_and_keeps_credentials_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);file=path/'sample.db'
            conn=sqlite3.connect(file);conn.execute('CREATE TABLE orders(id INTEGER)');conn.close()
            profiles=[{'id':'gcp','name':'Google BigQuery','kind':'bigquery','connection_env':'BQ_TEST_URL','schema':'analytics','allowed_tables':['orders'],'timeout_seconds':30},
                {'id':'aws','name':'Amazon Redshift','kind':'redshift','connection_env':'RS_TEST_URL','schema':'analytics','allowed_tables':['orders'],'timeout_seconds':20}]
            config=path/'sources.json';config.write_text(json.dumps(profiles),encoding='utf-8')
            registry=SourceRegistry(Database(file),config,environ={'BQ_TEST_URL':self.address,'RS_TEST_URL':'redshift://reader:secret@host/db'})
            with patch('langgraph_impl.bigquery_source.BigQueryDatabase') as bq,patch('langgraph_impl.warehouse.WarehouseDatabase') as rs:
                bq.return_value.allowed=rs.return_value.allowed={'orders'}
                registry.open('gcp');registry.open('aws')
                self.assertEqual(bq.call_args.kwargs['timeout'],30);self.assertEqual(rs.call_args.kwargs['timeout'],20)
            self.assertNotIn('secret',json.dumps(registry.list()));self.assertNotIn('demo-project',json.dumps(registry.list()))

    def test_all_new_dialects_preserve_security_predicates_on_sqlite_runtime(self):
        helper=connector_tests.WarehouseConnectorTests()
        for kind in ('mssql','oracle','snowflake','redshift','bigquery'):
            db=self.source()[0] if kind=='bigquery' else helper.adapter(kind)
            db.namespace='main'
            table='ORDERS' if kind in {'oracle','snowflake'} else 'orders'
            db.allowed={table};upper=kind in {'oracle','snowflake'}
            names=['ID','REGION'] if upper else ['id','region']
            db.security_projection={table:names}
            db.security_row_filters={table:{'sql':f'"__rls_b"."{names[1]}" = :__rls_region','parameters':{'__rls_region':'South'}}}
            if kind=='bigquery':db.security_row_filters[table]['sql']='`__rls_b`.`region` = :__rls_region'
            for sql in ('SELECT COUNT(*) FROM orders','WITH visible AS (SELECT * FROM orders) SELECT COUNT(*) FROM visible','SELECT COUNT(*) FROM orders a JOIN orders b ON a.id=b.id'):
                rendered,refs=db.checked_sql(sql,{},[table]);tree=sqlglot.parse_one(rendered,read={'mssql':'tsql'}.get(kind,kind))
                for item in tree.find_all(exp.Table):item.set('catalog',None)
                with sqlite3.connect(':memory:') as conn:
                    conn.execute('CREATE TABLE orders(id INTEGER,region TEXT,email TEXT)')
                    conn.executemany('INSERT INTO orders VALUES(?,?,?)',[(1,'South','private1'),(2,'North','private2')])
                    self.assertEqual(conn.execute(tree.sql(dialect='sqlite'),db.bound_parameters({},refs)).fetchall(),[(1,)])

    def test_configured_parent_policies_compile_for_every_new_engine(self):
        from langgraph_impl.row_security import RowSecurity
        helper=connector_tests.WarehouseConnectorTests()
        for kind in ('mssql','oracle','snowflake','redshift','bigquery'):
            db=self.source()[0] if kind=='bigquery' else helper.adapter(kind)
            db.namespace='main';native=lambda name:name.upper() if kind in {'oracle','snowflake'} else name
            orders,customers=native('orders'),native('customers');tenant,identifier,region,customer_id=map(native,('tenant','id','region','customer_id'))
            db.allowed={orders,customers}
            schemas={orders:[tenant,identifier,customer_id],customers:[tenant,identifier,region]}
            db.schema=lambda tables:{'tables':[{'table':table,'columns':[{'name':name,'type':'TEXT','primary_key':name in {tenant,identifier},'nullable':False} for name in schemas[table]],'foreign_keys':[]} for table in tables]}
            config={'version':1,'admin_bypass':False,'attributes':{'groups':{'limited':{'regions':['South']}}},'sources':{'remote':{
                customers:{'mode':'filtered','where':[{'column':region,'op':'in','attribute':'regions'}]},
                orders:{'mode':'filtered','parents':[{'table':customers,'columns':{tenant:tenant,customer_id:identifier}}]}}}}
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'rows.json';path.write_text(json.dumps(config),encoding='utf-8')
                rows=RowSecurity({'ROW_SECURITY_ENABLED':'true','ROW_SECURITY_FILE':str(path)})
                scope=rows.scope(db,'remote',{'role':'member','id':'a'*32,'groups':['limited']},{})
                policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
                view=protected_database(db,db.allowed,{},False,policy,'remote',lambda:None,scope)
                adapter=view.adapter();sql,refs=adapter.checked_sql('SELECT COUNT(*) FROM '+orders,{},[orders])
                tree=sqlglot.parse_one(sql,read={'mssql':'tsql'}.get(kind,kind))
                for item in tree.find_all(exp.Table):item.set('catalog',None)
                with sqlite3.connect(':memory:') as conn:
                    conn.execute('CREATE TABLE customers(tenant INTEGER,id INTEGER,region TEXT,PRIMARY KEY(tenant,id))')
                    conn.execute('CREATE TABLE orders(tenant INTEGER,id INTEGER,customer_id INTEGER)')
                    conn.executemany('INSERT INTO customers VALUES(?,?,?)',[(1,7,'South'),(2,7,'North')])
                    conn.executemany('INSERT INTO orders VALUES(?,?,?)',[(1,1,7),(2,2,7),(3,3,7)])
                    self.assertEqual(conn.execute(tree.sql(dialect='sqlite'),adapter.bound_parameters({},refs)).fetchall(),[(1,)])
