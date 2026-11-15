"""Isolated recorded demo: real graph, authorization UI and SQLite; scripted models."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]/'langgraph_impl'
sys.path.insert(0,str(PROJECT.parent))
TEMP=tempfile.TemporaryDirectory(prefix='querydesk-video-')
ROOT=Path(TEMP.name)
(ROOT/'.env').write_text('',encoding='utf-8')
(ROOT/'policy.json').write_text(json.dumps({'version':1,'sources':{'sample':{'classification':'internal'}},'profiles':[]}),encoding='utf-8')
os.environ['NLQ_ENV_FILE']=str(ROOT/'.env')
os.environ['MODEL_POLICY_FILE']=str(ROOT/'policy.json')
for name in ('LLM_API_KEY','OPENAI_API_KEY','GROQ_API_KEY','LANGSMITH_API_KEY','DATA_SOURCES_FILE','DOMAINS_FILE','UI_ALLOWED_TABLES'):
    os.environ.pop(name,None)
for name in ('LANGSMITH_TRACING','LANGCHAIN_TRACING','LANGCHAIN_TRACING_V2','ROW_SECURITY_ENABLED','PII_ENCRYPTION_ENABLED','AUTH_COOKIE_SECURE'):
    os.environ[name]='false'

from langgraph_impl import api
from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database
from langgraph_impl.workflow import LangGraphWorkflow
from langgraph_impl.access import protected_database
from langgraph_impl.pii import PIIPolicy

create_demo(ROOT/'sales.db')
database=Database(ROOT/'sales.db')
policy=PIIPolicy({'PII_ENCRYPTION_ENABLED':'false'})
TABLES=['orders','customers','customer_profiles','customer_segments','order_items','order_discounts','payments','refunds']
SQL='''WITH item_totals AS (
  SELECT order_id, SUM(quantity * unit_price_cents) AS gross
  FROM order_items GROUP BY order_id
), discount_totals AS (
  SELECT order_id, SUM(amount_cents) AS discount
  FROM order_discounts GROUP BY order_id
), payment_totals AS (
  SELECT order_id, SUM(amount_cents) AS captured
  FROM payments WHERE status = :success GROUP BY order_id
), refund_totals AS (
  SELECT p.order_id, SUM(r.amount_cents) AS refunded
  FROM refunds r JOIN payments p ON p.payment_id = r.payment_id
  WHERE r.status = :success GROUP BY p.order_id
)
SELECT COALESCE(s.name, 'Unassigned') AS Segment, c.region AS Region,
  COUNT(*) AS Orders,
  SUM(COALESCE(i.gross, 0)) / 100.0 AS Gross_INR,
  SUM(COALESCE(d.discount, 0)) / 100.0 AS Discounts_INR,
  SUM(COALESCE(p.captured, 0)) / 100.0 AS Payments_INR,
  SUM(COALESCE(r.refunded, 0)) / 100.0 AS Refunds_INR,
  SUM(COALESCE(p.captured, 0) - COALESCE(r.refunded, 0)) / 100.0 AS Net_Cash_INR,
  SUM(COALESCE(i.gross, 0) - COALESCE(d.discount, 0) - COALESCE(p.captured, 0)) / 100.0 AS Outstanding_INR
FROM orders o JOIN customers c ON c.customer_id = o.customer_id
LEFT JOIN customer_profiles cp ON cp.customer_id = c.customer_id
LEFT JOIN customer_segments s ON s.segment_id = cp.segment_id
LEFT JOIN item_totals i ON i.order_id = o.order_id
LEFT JOIN discount_totals d ON d.order_id = o.order_id
LEFT JOIN payment_totals p ON p.order_id = o.order_id
LEFT JOIN refund_totals r ON r.order_id = o.order_id
WHERE o.status <> :cancelled AND o.ordered_at >= :start_date AND o.ordered_at < :end_date
GROUP BY COALESCE(s.name, 'Unassigned'), c.region
ORDER BY Outstanding_INR DESC, Net_Cash_INR DESC, Segment, Region'''
PARAMS={'success':'succeeded','cancelled':'cancelled','start_date':'2026-09-01','end_date':'2026-12-01'}

class ScriptedModel:
    def generate(self,instructions,context,output_type):
        time.sleep(.35)
        kind=output_type.__name__
        if kind=='IntentOutput':return {'status':'ready','task':'Summarize non-cancelled sample orders by customer segment and region in INR.','assumptions':['Orders placed from September through November 2026; successful cash events relate to those orders.'],'message':''}
        if kind=='SchemaSelection':return {'tables':TABLES,'reason':'Orders join to customer profiles and segments. Item values, discounts, payments and refunds are aggregated separately to avoid double counting.'}
        if kind=='ColumnSelection':return {'tables':[{'table':table['table'],'columns':[c['name'] for c in table['columns']]} for table in context['schema']['tables']],'reason':'Retain the selected sample columns and join keys for this recorded report.'}
        if kind=='SQLSubmission':return {'sql':SQL,'parameters':[{'name':name,'value':value} for name,value in PARAMS.items()]}
        if kind=='ReviewOutput':return {'approved':True,'feedback':'Each cash/item stream is aggregated independently by order. Cancelled orders are excluded, dates are bound parameters, and paise are converted to INR.'}
        if kind=='AnswerOutput':return {'answer':'The report contains 10 segment/region groups covering 22 non-cancelled orders. New customers in the West have the largest outstanding balance: INR 680.','reason':'The query aggregates order items, applied discounts, successful payments and successful refunds separately before joining them to customer segments and regions. Outstanding balance is discounted order value minus successful payments; refunds are not added back as debt. Values are shown in INR for orders placed from September through November 2026.'}
        raise ValueError('Unsupported demo handoff')
    def close(self):pass

def workflow(query):
    # This recording has one synthetic administrator, with full sample access.
    view=protected_database(database,database.allowed,policy.columns['sample'],True,policy,'sample',lambda:None)
    return LangGraphWorkflow(view,model_factory=lambda role:ScriptedModel(),domain_hints=True)

api.ORIGINS.append('http://127.0.0.1:3217')
app=api.create_app(database,workflow_factory=workflow,record_path=ROOT/'records.db',confirmation_timeout=600,
    security_values={'NLQ_ENV_FILE':str(ROOT/'.env'),'MODEL_POLICY_FILE':str(ROOT/'policy.json'),
        'PII_ENCRYPTION_ENABLED':'false','ROW_SECURITY_ENABLED':'false','AUTH_COOKIE_SECURE':'false'})
