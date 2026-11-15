"""Local contract tests; no external credentials, network calls or warehouse claims."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock,patch

try:
    from sqlalchemy.engine import make_url
    import oracledb
except ImportError:
    raise unittest.SkipTest('Install optional warehouse requirements for connector checks')
import sqlglot
from sqlglot import exp

from langgraph_impl.connector_config import new_connection,WAREHOUSE_DIALECTS
from langgraph_impl.warehouse import WarehouseDatabase
from langgraph_impl.storage import QueryError,Database
from langgraph_impl.access import protected_database
from langgraph_impl.pii import PIIPolicy
from langgraph_impl.contracts import Candidate
from langgraph_impl.planning import manifest


class WarehouseConnectorTests(unittest.TestCase):
    urls={'mssql':'mssql+pytds://reader:secret@db.example.test/reporting',
        'redshift':'redshift+psycopg2://reader:secret@db.example.test/reporting',
        'oracle':'oracle+oracledb://reader:secret@db.example.test/?service_name=REPORTING',
        'snowflake':'snowflake://reader:secret@org-account/REPORTING/ANALYTICS?warehouse=QUERY_WH&role=QUERY_READER'}

    def adapter(self,kind):
        db=WarehouseDatabase.__new__(WarehouseDatabase)
        db.kind,db.namespace=kind,'dbo' if kind=='mssql' else 'ANALYTICS'
        db.allowed={'orders','customers'} if kind in {'mssql','redshift'} else {'ORDERS','CUSTOMERS'}
        db.row_limit,db.timeout=100,3
        return db

    def test_connection_options_pin_drivers_transport_and_timeouts(self):
        for kind,address in self.urls.items():
            url,schema,args=new_connection(make_url(address),kind,'dbo' if kind=='mssql' else 'ANALYTICS',3)
            if kind=='mssql':
                self.assertTrue(args['validate_host']);self.assertFalse(args['enc_login_only'])
                self.assertTrue(args['readonly']);self.assertTrue(args['cafile'])
            if kind=='oracle':
                self.assertEqual(args['protocol'],'tcps');self.assertEqual(args['tcp_connect_timeout'],5)
                self.assertIn('(PROTOCOL=TCPS)',args['dsn']);self.assertTrue(args['ssl_server_dn_match'])
            if kind=='snowflake':self.assertEqual(args['session_parameters']['STATEMENT_TIMEOUT_IN_SECONDS'],3)

    def test_unsafe_connection_options_fail_before_connecting(self):
        for kind,address in self.urls.items():
            with self.subTest(kind=kind),patch('sqlalchemy.create_engine') as engine:
                with self.assertRaises(QueryError):WarehouseDatabase(address+'&evil=1' if '?' in address else address+'?evil=1',kind,['orders'],'ANALYTICS')
                engine.assert_not_called()
        for address in (self.urls['snowflake'].replace('QUERY_READER','ACCOUNTADMIN'),self.urls['snowflake'].replace('ANALYTICS','OTHER'),
            self.urls['oracle'].replace('service_name=REPORTING','dsn=localfile')):
            kind='snowflake' if address.startswith('snowflake') else 'oracle'
            with self.assertRaises(ValueError):new_connection(make_url(address),kind,'ANALYTICS',3)

    def test_dialect_specific_caps_binds_ctes_and_scope(self):
        for kind in self.urls:
            db=self.adapter(kind)
            table='orders' if kind in {'mssql','redshift'} else 'ORDERS'
            query,refs=db.checked_sql('WITH recent AS (SELECT * FROM orders WHERE amount>:minimum) SELECT COUNT(*) FROM recent',{'minimum':Decimal('1.20')},[table])
            self.assertEqual(refs,{table});self.assertIn(':minimum',query)
            tree=sqlglot.parse_one(query,read=WAREHOUSE_DIALECTS[kind])
            self.assertIsNotNone(tree.args.get('limit'));self.assertIn('101',query)
            for sql in ('DELETE FROM orders','SELECT * FROM private.orders','SELECT * FROM otherdb.ANALYTICS.orders',
                'SELECT * FROM orders; SELECT 1','SELECT dangerous(amount) FROM orders','SELECT * INTO leak FROM orders'):
                with self.subTest(kind=kind,sql=sql),self.assertRaises(QueryError):db.checked_sql(sql,{},[table])

    def test_literal_limits_preserved_and_large_limits_capped(self):
        for kind,clause in (('mssql','TOP 5'),('oracle',''),('snowflake','')):
            db=self.adapter(kind);table='orders' if kind in {'mssql','redshift'} else 'ORDERS'
            sql='SELECT TOP 5 * FROM orders' if kind=='mssql' else 'SELECT * FROM orders FETCH FIRST 5 ROWS ONLY' if kind=='oracle' else 'SELECT * FROM orders LIMIT 5'
            query,_=db.checked_sql(sql,{},[table]);self.assertIn('5',query);self.assertNotIn('101',query)
            sql=sql.replace('5','1000000');query,_=db.checked_sql(sql,{},[table]);self.assertIn('101',query)
        db=self.adapter('mssql')
        with self.assertRaises(QueryError):db.checked_sql('SELECT TOP 5 PERCENT * FROM orders',{},['orders'])

    def test_security_projection_and_rows_in_every_base_reference(self):
        for kind in self.urls:
            db=self.adapter(kind);table='orders' if kind in {'mssql','redshift'} else 'ORDERS'
            columns=['id','region'] if kind in {'mssql','redshift'} else ['ID','REGION']
            db.security_projection={table:columns}
            db.security_row_filters={table:{'sql':'"__rls_b"."'+columns[1]+'" = :__rls_region','parameters':{'__rls_region':'South'}}}
            query,refs=db.checked_sql('SELECT COUNT(*) FROM orders a JOIN orders b ON a.id=b.id',{},[table])
            self.assertEqual(query.count(':__rls_region'),2)
            tree=sqlglot.parse_one(query,read=WAREHOUSE_DIALECTS[kind])
            projections=list(tree.find_all(exp.Subquery));self.assertEqual(len(projections),2)
            self.assertNotIn('email',query.lower())
            self.assertEqual(db.bound_parameters({},refs),{'__rls_region':'South'})
            with self.assertRaises(QueryError):db.checked_sql('SELECT * FROM orders WHERE region=:__rls_region',{'__rls_region':'West'},[table])

    def test_new_connections_rollback_and_set_driver_deadlines(self):
        for kind in ('mssql','snowflake','oracle','redshift'):
            db=self.adapter(kind);conn=MagicMock();db.engine=MagicMock()
            db.engine.connect.return_value.__enter__.return_value=conn
            with db.connection():pass
            conn.rollback.assert_called_once()
            statements=[str(call.args[0]) for call in conn.execute.call_args_list]
            if kind in {'oracle','redshift'}:self.assertIn('SET TRANSACTION READ ONLY',statements)
            if kind=='oracle':
                self.assertEqual(conn.connection.driver_connection.call_timeout,3000)
                import oracledb
                cursor=MagicMock();cursor.arraysize=100
                conn.connection.driver_connection.outputtypehandler(cursor,MagicMock(type_code=oracledb.DB_TYPE_NUMBER))
                self.assertIs(cursor.var.call_args.kwargs['outconverter'],Decimal)
            if kind=='redshift':self.assertIn('SET statement_timeout = 3000',statements)

    def test_planning_is_metadata_only_and_approval_contains_effective_protection(self):
        for kind in self.urls:
            db=self.adapter(kind);table='orders' if kind in {'mssql','redshift'} else 'ORDERS'
            db.schema=lambda tables:{'tables':[{'table':table,'columns':[{'name':'ID'}],'foreign_keys':[]}]}
            policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
            view=protected_database(db,{table},{},False,policy,'connected',lambda:None)
            with patch.object(db,'connection',side_effect=AssertionError('No source reads during planning')):
                result=manifest(view,Candidate.parse({'sql':'SELECT COUNT(*) FROM orders','parameters':[]}),[table])
            self.assertEqual(result['execution_mode'],'direct');self.assertIn('101',result['sql'])
            self.assertFalse(result['adapter_capabilities']['local_pii_decryption'])
            self.assertIn('ID',result['sql'])

    def test_results_keep_decimal_precision_and_close_cursor(self):
        for kind in self.urls:
            db=self.adapter(kind);table='orders' if kind in {'mssql','redshift'} else 'ORDERS'
            cursor=MagicMock();cursor.keys.return_value=['amount'];cursor.fetchmany.return_value=[(Decimal('12345678901234567890.00100'),)]
            conn=MagicMock();conn.execution_options.return_value=conn;conn.execute.return_value=cursor
            @contextmanager
            def connection():yield conn
            db.connection=connection
            result=db.execute('SELECT amount FROM orders',{},[table])
            self.assertEqual(result['rows'],[[Decimal('12345678901234567890.00100')]])
            cursor.close.assert_called_once();cursor.fetchmany.assert_called_once_with(101)

    def test_driver_construction_and_metadata_discovery_are_registered(self):
        for kind,address in self.urls.items():
            inspector=MagicMock();inspector.get_table_names.return_value=['orders']
            with patch('sqlalchemy.create_engine') as engine,patch('sqlalchemy.inspect',return_value=inspector):
                WarehouseDatabase(address,kind,['ORDERS'] if kind in {'oracle','snowflake'} else ['orders'],'ANALYTICS')
                args=engine.call_args
                self.assertTrue(args.kwargs['hide_parameters']);self.assertFalse(args.kwargs['echo'])
                self.assertEqual(args.args[0].drivername,make_url(address).drivername)

    def test_reflection_restores_physical_uppercase_without_changing_quoted_names(self):
        from sqlalchemy.sql import quoted_name
        for kind in ('oracle','snowflake'):
            db=self.adapter(kind)
            self.assertEqual(db.physical_name('orders'),'ORDERS')
            self.assertEqual(db.physical_name(quoted_name('orders',True)),'orders')
            self.assertEqual(db.physical_name('MixedCase'),'MixedCase')

    def test_installed_dialects_construct_real_engines_without_connecting(self):
        from sqlalchemy import create_engine,event
        from sqlalchemy.pool import NullPool
        for kind,address in self.urls.items():
            url,_,args=new_connection(make_url(address),kind,'ANALYTICS',3)
            engine=create_engine(url,poolclass=NullPool,connect_args=args,hide_parameters=True)
            try:
                self.assertEqual(engine.dialect.name,kind)
                positional,keywords=engine.dialect.create_connect_args(url)
                self.assertTrue(positional or keywords)
                if kind=='oracle':
                    captured={}
                    def intercept(dialect,record,cargs,cparams):
                        captured.update(cparams)
                        raise RuntimeError('Stop before network access')
                    event.listen(engine,'do_connect',intercept)
                    with self.assertRaises(RuntimeError):engine.connect()
                    self.assertIn('(PROTOCOL=TCPS)',captured['dsn'])
            finally:engine.dispose()
