from langgraph_impl.tests.auth_helpers import sign_in_admin
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock,patch

from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database,QueryError
from langgraph_impl.sources import SourceRegistry
from langgraph_impl.warehouse import WarehouseDatabase

try:
    import sqlalchemy
    import sqlglot
except ImportError:
    sqlalchemy=None


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        create_demo(self.directory/"sales.db")
        self.db=Database(self.directory/"sales.db")
        self.profiles=self.directory/"sources.json"
        self.profile={"id":"other","name":"Other database","kind":"sqlite","connection_env":"OTHER_DB",
            "allowed_tables":["orders"]}
        self.profiles.write_text(json.dumps([self.profile]))

    def test_profile_selection_scope_and_no_sample_context_leakage(self):
        registry=SourceRegistry(self.db,self.profiles,environ={"OTHER_DB":str(self.db.filename)})
        self.assertEqual(len(registry.list()),2)
        self.assertNotIn(str(self.db.filename),str(registry.list()))
        other,knowledge=registry.open("other")
        self.assertEqual(other.allowed,{"orders"})
        self.assertEqual(other.execute("SELECT COUNT(*) FROM orders",{},["orders"])["rows"],[[25]])
        with self.assertRaises(QueryError):
            other.schema(["payments"])
        self.assertEqual(knowledge.examples,[])
        self.assertEqual(knowledge.context("cash INR",other.allowed)["metrics"],[])
        self.assertEqual(knowledge.route("orders",other.allowed)["workspaces"],["database"])
        self.assertTrue(registry.test("other")["connected"])
        with self.assertRaises(QueryError):
            registry.open("unknown")

    def test_bad_profiles_and_unconfigured_connections_fail_closed(self):
        for change in ({"id":"sample"},{"allowed_tables":[]},{"kind":"untrusted_plugin"},{"password":"secret"}):
            self.profiles.write_text(json.dumps([{**self.profile,**change}]))
            with self.assertRaises(ValueError):
                SourceRegistry(self.db,self.profiles,environ={})
        self.profiles.write_text(json.dumps([self.profile]))
        registry=SourceRegistry(self.db,self.profiles,environ={"OTHER_DB":""})
        self.assertFalse(registry.list()[1]["configured"])
        with self.assertRaisesRegex(QueryError,"not configured"):
            registry.open("other")

    def test_api_lists_tests_and_routes_source_metadata_without_secrets(self):
        from fastapi.testclient import TestClient
        from langgraph_impl.api import create_app
        with patch.dict(os.environ,{"OTHER_DB":str(self.db.filename)}):
            app=create_app(self.db,workflow_factory=lambda query:None,profiles_file=self.profiles,
                record_path=self.directory/"records.sqlite3")
        with TestClient(app) as client:
            sign_in_admin(client)
            self.assertEqual(len(client.get("/api/sources").json()["sources"]),2)
            config=client.get("/api/config?source=other").json()
            self.assertEqual(config["table_count"],1)
            self.assertEqual(config["suggestions"],[])
            self.assertEqual(config["workspaces"][0]["id"],"database")
            self.assertEqual(client.get("/api/schema?source=other").json()["tables"][0]["table"],"orders")
            self.assertEqual(client.get("/api/metrics?source=other").json()["metrics"],[])
            self.assertEqual(client.post("/api/sources/other/test",json={}).status_code,200)
            self.assertEqual(client.get("/api/config?source=private").status_code,404)
            self.assertEqual(client.post("/api/queries",json={"question":"Count orders","source":"other","workspace":"sales"}).status_code,422)


@unittest.skipIf(sqlalchemy is None,"Install requirements-sources.txt for optional connector checks")
class WarehouseTests(unittest.TestCase):
    def test_security_projection_prevents_star_alias_and_predicate_pii_bypasses(self):
        from sqlalchemy import create_engine,text
        engine=create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE customers(customer_id INTEGER PRIMARY KEY,name TEXT,email TEXT,region TEXT)'))
            conn.execute(text("INSERT INTO customers VALUES(1,'Mira','customer01@customers.example.test','South')"))
        for kind in ('postgresql','mysql'):
            db=self.adapter(kind)
            db.allowed={'customers'}
            db.namespace='main'
            db.security_projection={'customers':['customer_id','region']}
            @contextmanager
            def connection():
                with engine.connect() as conn:yield conn
            db.connection=connection
            result=db.execute('SELECT * FROM customers',{},['customers'])
            self.assertEqual(result['columns'],['customer_id','region'])
            self.assertEqual(result['rows'],[[1,'South']])
            self.assertEqual(db.execute('SELECT main.customers.customer_id FROM main.customers',{},['customers'])['rows'],[[1]])
            for sql in ('SELECT name AS harmless FROM customers',"SELECT COUNT(*) FROM customers WHERE email='customer01@customers.example.test'"):
                with self.subTest(kind=kind,sql=sql),self.assertRaises(Exception):db.execute(sql,{},['customers'])

    def adapter(self,kind):
        adapter=WarehouseDatabase.__new__(WarehouseDatabase)
        adapter.kind,adapter.allowed,adapter.namespace=kind,{"orders","order_items"},"analytics"
        adapter.row_limit,adapter.timeout=100,3
        return adapter

    def test_both_dialects_scope_ctes_named_binds_and_security(self):
        for kind in ("postgresql","mysql"):
            db=self.adapter(kind)
            query,tables=db.checked_sql("WITH recent AS (SELECT * FROM orders WHERE status=:status) SELECT COUNT(*) FROM recent",{"status":"completed"},["orders"])
            self.assertEqual(tables,{"orders"})
            self.assertIn(":status",query)
            self.assertNotIn("%(status)s",query)
            self.assertIn("analytics",query)
            self.assertIn("LIMIT 101",query)
            limited,_=db.checked_sql("SELECT * FROM orders LIMIT 5",{},["orders"])
            self.assertIn("LIMIT 5",limited)
            capped,_=db.checked_sql("SELECT * FROM orders LIMIT 1000000",{},["orders"])
            self.assertIn("LIMIT 101",capped)
            for sql,parameters in [
                ("SELECT * FROM payments",{}),("SELECT * FROM private.orders",{}),
                ("SELECT * FROM orders; DROP TABLE orders",{}),
                ("WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone",{}),
                ("SELECT * INTO backup FROM orders",{}),("SELECT * FROM orders FOR UPDATE",{}),
                ("SELECT load_file('/secret')",{}),("SELECT pg_read_file('/secret')",{}),
                ("SELECT evil.sum(1)",{}),("SELECT * FROM orders WHERE status=:missing",{}),
                ("SELECT * FROM orders",{"unused":1}),
            ]:
                with self.subTest(kind=kind,sql=sql),self.assertRaises(QueryError):
                    db.checked_sql(sql,parameters,["orders"])

    def test_source_connections_request_read_only_and_bounded_execution(self):
        for kind in ("postgresql","mysql"):
            db=self.adapter(kind)
            conn=MagicMock()
            conn.execution_options.return_value=conn
            db.engine=MagicMock()
            db.engine.connect.return_value.__enter__.return_value=conn
            with db.connection() as actual:
                self.assertIs(actual,conn)
            statements=[str(call.args[0]) for call in conn.execute.call_args_list]
            if kind=="postgresql":
                conn.execution_options.assert_called_once_with(postgresql_readonly=True)
                self.assertIn("SET LOCAL statement_timeout = 3000",statements)
            else:
                self.assertIn("SET SESSION TRANSACTION READ ONLY",statements)
                self.assertIn("SET SESSION max_execution_time = 3000",statements)
                conn.rollback.assert_called_once()

    def test_result_normalization_and_partial_data_disclosure(self):
        db=self.adapter("postgresql")
        cursor=MagicMock()
        cursor.keys.return_value=["amount","date","large","binary"]
        cursor.fetchmany.return_value=[(Decimal('12345678901234567890.00100'),date(2026,11,13),'x'*2100,b'bytes')]
        conn=MagicMock()
        conn.execute.return_value=cursor
        conn.execution_options.return_value=conn
        @contextmanager
        def connection():
            yield conn
        db.connection=connection
        result=db.execute("SELECT amount FROM orders",{},["orders"])
        self.assertEqual(result["rows"][0][:2],[Decimal('12345678901234567890.00100'),'2026-11-13'])
        self.assertIsInstance(result['rows'][0][0],Decimal)
        self.assertTrue(result["truncated"])

    def test_connection_failures_do_not_expose_url_credentials(self):
        with patch('sqlalchemy.create_engine',side_effect=RuntimeError('password=secret123')):
            with self.assertRaises(QueryError) as raised:
                WarehouseDatabase('postgresql://reader:secret123@localhost/analytics','postgresql',['orders'])
        self.assertNotIn('secret123',str(raised.exception))
