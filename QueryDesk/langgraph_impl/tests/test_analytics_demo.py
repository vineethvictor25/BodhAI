"""Independent integrity, governance and rendering checks for the analytics lab."""
from contextlib import closing
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from langgraph_impl.analytics_demo import build_lab, example_queries, file_hash, month_add, register_local, validate
from langgraph_impl.access import protected_database
from langgraph_impl.pii import PIIPolicy
from langgraph_impl.row_security import RowSecurity
from langgraph_impl.sources import SourceRegistry
from langgraph_impl.sqlserver_multi import validate_mappings
from langgraph_impl.storage import Database
from langgraph_impl.visualization import profile_result, validate_plan, render_figure


class AnalyticsLabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.lab = cls.root/'lab'
        cls.settings = dict(customers=100, months=18, orders_per_day=5)
        cls.manifest = build_lab(cls.lab, **cls.settings)
        cls.db = Database(cls.lab/'analytics_mirror.db', row_limit=500, timeout=15)

    def connection(self):
        conn = sqlite3.connect((self.lab/'analytics_mirror.db').as_uri()+'?mode=ro', uri=True)
        self.addCleanup(conn.close)
        return conn

    def test_reproducible_and_existing_data_is_never_overwritten(self):
        other = self.root/'repeat'
        second = build_lab(other, **self.settings)
        self.assertEqual(self.manifest['database_sha256'], second['database_sha256'])
        self.assertEqual(build_lab(other, **self.settings), second)
        with self.assertRaises(FileExistsError):
            build_lab(other, **{**self.settings, 'seed':1})
        with (other/'commerce.db').open('ab') as stream:
            stream.write(b'edited')
        before = file_hash(other/'commerce.db')
        with self.assertRaises(FileExistsError):
            build_lab(other, **self.settings)
        self.assertEqual(file_hash(other/'commerce.db'), before)
        partial = self.root/'partial'; partial.mkdir()
        with self.assertRaises(FileExistsError):
            build_lab(partial, **self.settings)

    def test_relational_and_financial_invariants(self):
        checks = validate(self.connection())
        self.assertEqual(len(checks),13)
        self.assertTrue(all(checks.values()))
        self.assertEqual(len(self.db.allowed),20)
        self.assertGreater(self.manifest['total_rows'],20000)

    def test_two_files_really_join_and_match_mirror(self):
        with closing(sqlite3.connect(':memory:',uri=True)) as conn:
            for alias, filename in [('c','commerce.db'),('i','intelligence.db')]:
                conn.execute('ATTACH DATABASE ? AS '+alias, ((self.lab/filename).as_uri()+'?mode=ro',))
            rows = conn.execute("SELECT s.segment_name,COUNT(*),SUM(o.net_sales_cents) FROM c.orders o JOIN c.customers c ON c.customer_id=o.customer_id JOIN i.segments s ON s.segment_id=c.segment_id WHERE o.status='completed' GROUP BY s.segment_name ORDER BY 1").fetchall()
        expected = self.connection().execute("SELECT s.segment_name,COUNT(*),SUM(o.net_sales_cents) FROM commerce_orders o JOIN commerce_customers c ON c.customer_id=o.customer_id JOIN intelligence_segments s ON s.segment_id=c.segment_id WHERE o.status='completed' GROUP BY s.segment_name ORDER BY 1").fetchall()
        self.assertEqual(rows,expected)

    def test_reference_queries_work_through_read_only_boundary(self):
        refs = json.loads((self.lab/'reference_answers.json').read_text(encoding='utf-8'))
        self.assertEqual(len(refs),12)
        for reference, (key,_,sql,tables) in zip(refs,example_queries()):
            with self.subTest(query=key):
                result = self.db.execute(sql,{},tables)
                self.assertFalse(result['truncated'])
                self.assertEqual(result['columns'],reference['columns'])
                self.assertEqual(result['rows'],reference['rows'])

    def test_features_and_labels_are_as_of_observed_dates(self):
        conn = self.connection()
        for month,customer,orders,sales in conn.execute('SELECT snapshot_month,customer_id,completed_orders,net_sales_cents FROM intelligence_customer_month_features ORDER BY snapshot_month,customer_id LIMIT 50'):
            end = month_add(date.fromisoformat(month),1).isoformat()
            actual = conn.execute("SELECT COUNT(*),COALESCE(SUM(net_sales_cents),0) FROM commerce_orders WHERE customer_id=? AND status='completed' AND order_date>=? AND order_date<?",(customer,month,end)).fetchone()
            self.assertEqual(actual,(orders,sales))
        for month,customer,label,orders,sales,inactive in conn.execute('SELECT * FROM intelligence_customer_month_outcomes ORDER BY snapshot_month,customer_id LIMIT 50'):
            self.assertEqual(label,month_add(date.fromisoformat(month),1).isoformat())
            actual = conn.execute("SELECT COUNT(*),COALESCE(SUM(net_sales_cents),0) FROM commerce_orders WHERE customer_id=? AND status='completed' AND order_date>=? AND order_date<?",(customer,label,month_add(date.fromisoformat(label),1).isoformat())).fetchone()
            self.assertEqual(actual,(orders,sales)); self.assertEqual(inactive,int(orders==0))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM intelligence_customer_month_outcomes WHERE snapshot_month='2024-06-01'").fetchone()[0],0)

    def test_forecasts_use_only_available_history(self):
        conn = self.connection()
        for origin,target,region,category,horizon,cutoff,predicted in conn.execute('SELECT origin_month,target_month,region_id,category_id,horizon_months,training_max_date,forecast_units FROM intelligence_sales_forecasts ORDER BY forecast_id LIMIT 48'):
            beginning = date.fromisoformat(origin)
            self.assertEqual(cutoff,(month_add(beginning,1)-timedelta(days=1)).isoformat())
            self.assertEqual(target,month_add(beginning,horizon).isoformat())
            def units(month):
                row = conn.execute('SELECT units FROM commerce_monthly_sales WHERE month_date=? AND region_id=? AND category_id=?',(month.isoformat(),region,category)).fetchone()
                return row[0] if row else 0
            prior = sum(units(month_add(beginning,-12-k)) for k in range(3))
            recent = sum(units(month_add(beginning,-k)) for k in range(3))
            growth = min(1.4,max(.7,recent/prior)) if prior else 1.
            self.assertEqual(predicted,round(units(month_add(date.fromisoformat(target),-12))*growth))

    def test_registered_source_and_native_mappings(self):
        registry = SourceRegistry(self.db, profiles_file=self.lab/'sources.local.json', environ={'ANALYTICS_LAB_DATABASE':str(self.lab/'analytics_mirror.db')})
        db, knowledge = registry.open('analytics_lab')
        self.assertEqual(db.allowed,self.db.allowed)
        self.assertEqual(len(knowledge.examples),12)
        profile = json.loads((self.lab/'sources.sqlserver.json').read_text(encoding='utf-8'))[0]
        mappings, relations = validate_mappings(profile['allowed_tables'],profile['table_mappings'],profile['relationships'])
        self.assertEqual(len(mappings),20)
        self.assertTrue(any(mappings[r['from_table']]['database']!=mappings[r['to_table']]['database'] for r in relations))
        import sqlglot
        for item in json.loads((self.lab/'knowledge_sqlserver/examples.json').read_text(encoding='utf-8')):
            self.assertIsNotNone(sqlglot.parse_one(item['sql'],read='tsql'))
        seed = (self.lab/'sqlserver_setup.sql').read_text(encoding='utf-8')
        self.assertIn('CREATE DATABASE [AnalyticsCommerce]',seed)
        self.assertIn('CREATE DATABASE [AnalyticsIntelligence]',seed)
        self.assertIn('THROW',seed); self.assertIn('CREATE INDEX',seed)
        self.assertNotIn('DROP DATABASE',seed)

    def test_region_rules_apply_across_both_domains_and_deny_missing_scope(self):
        snippets = json.loads((self.lab/'row_security.sources.json').read_text(encoding='utf-8'))
        policy = self.root/'rows.json'
        policy.write_text(json.dumps({'version':1,'admin_bypass':True,'attributes':{'groups':{'south':{'regions':['South']}}},'sources':snippets}),encoding='utf-8')
        rows = RowSecurity({'ROW_SECURITY_ENABLED':'true','ROW_SECURITY_FILE':str(policy)})
        pii = PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
        for groups, expected in [(['south'],1),(['business_users'],0)]:
            user={'id':'a'*32,'username':'analyst','role':'member','groups':groups}
            view=protected_database(self.db,self.db.allowed,{'commerce_customers':['email','phone']},False,pii,'analytics_lab',lambda:None,row_scope=rows.scope(self.db,'analytics_lab',user,{'commerce_customers':['email','phone']}))
            sql='SELECT COUNT(DISTINCT r.region_name) region_count FROM intelligence_customer_month_features f JOIN commerce_customers c ON c.customer_id=f.customer_id JOIN commerce_regions r ON r.region_id=c.region_id'
            self.assertEqual(view.execute(sql,{},['intelligence_customer_month_features','commerce_customers','commerce_regions'])['rows'],[[expected]])

    def test_trend_bar_kpi_and_scatter_render_with_existing_chart_controls(self):
        for key,kind,x,y in [('monthly_net_sales','line',0,1),('regional_sales','bar',0,1),('net_collected','kpi',None,0)]:
            _,_,sql,tables = next(item for item in example_queries() if item[0]==key)
            data = self.db.execute(sql,{},tables)
            result,profile = profile_result(data)
            raw={'status':'chart','kind':kind,'library':'plotly','title':'Synthetic analytics','reason':'Compare observed synthetic results.','x_column':x,'y_column':y,'suggestions':[]}
            self.assertEqual(render_figure(validate_plan(raw,result,profile),result)['status'],'chart')
        data=self.db.execute("SELECT recency_days,net_sales_cents/100.0 net_sales_inr FROM intelligence_customer_month_features WHERE snapshot_month='2024-06-01' AND recency_days IS NOT NULL LIMIT 400",{},['intelligence_customer_month_features'])
        result,profile=profile_result(data)
        raw={'status':'chart','kind':'scatter','library':'plotly','title':'Recency and sales','reason':'Compare two metrics.','x_column':0,'y_column':1,'suggestions':[]}
        self.assertEqual(render_figure(validate_plan(raw,result,profile),result)['status'],'chart')

    def test_registration_preserves_existing_security_settings(self):
        from langgraph_impl.config import APP_DIR
        root=self.root/'app';root.mkdir()
        for name in ['model_policy.json','encryption.json','row_security.json']:
            (root/name).write_bytes((APP_DIR/name).read_bytes())
        (root/'.env').write_text('ROW_SECURITY_ENABLED=true\nPII_ENCRYPTION_ENABLED=false\n',encoding='utf-8')
        before={name:json.loads((root/name).read_text(encoding='utf-8')) for name in ['model_policy.json','encryption.json','row_security.json']}
        # Fresh fake installation keeps all other source policies but has no analytics lab.
        for name,data in before.items():
            data['sources'].pop('analytics_lab',None)
            (root/name).write_text(json.dumps(data),encoding='utf-8')
        with patch('langgraph_impl.analytics_demo.APP_DIR',root),patch('langgraph_impl.config.environment_values',return_value={}):
            result=register_local(self.lab)
        self.assertFalse(result['provider_approvals_changed'])
        for name,data in before.items():
            after=json.loads((root/name).read_text(encoding='utf-8'))
            after['sources'].pop('analytics_lab')
            self.assertEqual(after,data)
        self.assertTrue((root/'.env').read_text(encoding='utf-8').startswith('ROW_SECURITY_ENABLED=true\nPII_ENCRYPTION_ENABLED=false\n'))


if __name__=='__main__':unittest.main()
