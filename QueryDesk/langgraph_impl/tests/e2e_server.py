import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

_TEMP=tempfile.TemporaryDirectory(prefix="querydesk-e2e-")
_ROOT=Path(_TEMP.name)
(_ROOT/".env").write_text("",encoding="utf-8")
(_ROOT/"model_policy.json").write_text(json.dumps({
    "version":1,"sources":{"sample":{"classification":"internal"}},"profiles":[]
}),encoding="utf-8")
os.environ["NLQ_ENV_FILE"]=str(_ROOT/".env")
os.environ["MODEL_POLICY_FILE"]=str(_ROOT/"model_policy.json")
os.environ["ROW_SECURITY_ENABLED"]="false"
os.environ["AUTH_COOKIE_SECURE"]="false"
os.environ["AUTH_SETUP_TOKEN"]="local-e2e-setup-secret-2026-11-15"
for _key in ("LLM_API_KEY","OPENAI_API_KEY","GROQ_API_KEY","LANGSMITH_API_KEY"):
    os.environ.pop(_key,None)
for _key in ("LANGSMITH_TRACING","LANGCHAIN_TRACING","LANGCHAIN_TRACING_V2"):
    os.environ.pop(_key,None)

from langgraph_impl import api
from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database

api.ORIGINS.append(f"http://127.0.0.1:{os.environ.get('QUERYDESK_E2E_UI_PORT','3211')}")
_database_path=_ROOT/"sales.db"
create_demo(_database_path)
_database=Database(_database_path)
_sql="SELECT COUNT(*) FROM orders;"


class DeterministicWorkflow:
    def advance(self,question,*,state=None,decision=None,on_event=None):
        sql='SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status ORDER BY status;' if 'by status' in question.lower() else _sql
        if state is None:
            return {"waiting":"tables","state":{"phase":"tables"},"proposal":{
                "tables":["orders"],"available_tables":["orders"],"workspaces":["sales"],
                "reason":"Count the rows in the sample orders table."}}
        if state["phase"]=="tables":
            if decision["tables"]!=["orders"]:
                raise ValueError("The fixture question requires the orders table.")
            on_event({"stage":"schema","status":"completed","tables":["orders"]})
            on_event({"stage":"sql","status":"completed","sql":sql,"parameters":{}})
            return {"waiting":"sql","state":{"phase":"sql"},"proposal":{
                "sql":sql,"parameters":{},"tables":["orders"],"fingerprint":"d"*64}}
        if state["phase"]=="sql" and decision["kind"]=="sql":
            on_event({"stage":"execution","status":"running"})
            result=_database.execute(sql,{},["orders"])
            count=result["rows"][0][0]
            on_event({"stage":"execution","status":"completed"})
            return {"result":{
                "status":"answered","answer":'Orders by status are shown.' if sql!=_sql else f"There are {count} orders.",
                "reason":"Every row in the orders table was counted.",
                "tables":["orders"],"queries":[{"sql":sql,"parameters":{},"result":result}],
                "assumptions":[],"workspaces":["sales"],"attempts":1,"model_calls":0,
                "example_ids":[],"pruning":{}}}
        raise ValueError("Unexpected deterministic workflow transition.")


class DeterministicVisualizationModel:
    def generate(self,instructions,context,output_type):
        single=context['profile']['row_count']==1
        return {'status':'chart','library':'seaborn' if context.get('presentation')=='static' else 'plotly','kind':'kpi' if single else 'bar','title':'Order count' if single else 'Orders by status',
            'reason':'Visualize the returned counts.','x_column':None if single else 0,'y_column':0 if single else 1,'suggestions':[]}
    def close(self):pass


app=api.create_app(
    database=_database,
    workflow_factory=lambda query:DeterministicWorkflow(),
    visualization_model_factory=DeterministicVisualizationModel,
    record_path=_ROOT/"reviews.sqlite3",
    confirmation_timeout=60,
    security_values={
        "NLQ_ENV_FILE":str(_ROOT/".env"),
        "MODEL_POLICY_FILE":str(_ROOT/"model_policy.json"),
        "ROW_SECURITY_ENABLED":"false",
        "AUTH_COOKIE_SECURE":"false",
        "AUTH_SETUP_TOKEN":"local-e2e-setup-secret-2026-11-15",
        "HISTORY_RETENTION_HOURS":"1",
    },
)
