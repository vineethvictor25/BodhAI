import ast
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError
from openai import BadRequestError

from langgraph_impl import config
from langgraph_impl.agents import SchemaAgent, ValidationResult
from langgraph_impl.config import ModelConfig
from langgraph_impl.contracts import Approval, ApprovalRegistry, Budget, Candidate, IntentOutput
from langgraph_impl.demo import create_demo
from langgraph_impl.models import LangChainModel
from langgraph_impl.storage import Database, QueryError
from langgraph_impl.workflow import LangGraphWorkflow


APP = Path(__file__).resolve().parents[1]
READY = {"status": "ready", "task": "Count all orders", "assumptions": [], "message": ""}
COUNT = {"sql": "SELECT COUNT(*) FROM orders", "parameters": []}


class FixtureModel:
    def __init__(self, calls):
        self.calls = iter(calls)
        self.contexts = []

    def generate(self, instructions, context, output_type):
        self.contexts.append(context)
        if output_type.__name__ == "ColumnSelection":
            return {"tables":[{"table":t["table"],"columns":[]} for t in context["schema"]["tables"]],
                    "reason":"Fixture: deterministic retention restores required columns"}
        return next(self.calls)


class IndependentGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sales.db"
        create_demo(self.path, expanded=False)
        self.db = Database(self.path)

    def scripts(self, attempts=1):
        return {"intent": [READY],
                "schema": [{"tables": ["orders"], "reason": "Count order records"}] * attempts,
                "sql": [COUNT] * attempts,
                "validation": [{"approved": True, "feedback": "Correct count"}] * attempts,
                "response": [{"answer": "There are 5 orders.","reason":"The executed query counted all order rows."}],"columns":[]}

    def workflow(self, scripts=None, database=None, **kwargs):
        self.models = {role: FixtureModel(calls) for role, calls in (scripts or self.scripts()).items()}
        return LangGraphWorkflow(database or self.db, model_factory=lambda role: self.models[role], **kwargs)

    def test_graph_executes_every_stage_via_orchestrator(self):
        app = self.workflow()
        self.assertIsInstance(app.build_graph(), CompiledStateGraph)
        result = app.ask("How many orders?")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["queries"][0]["result"]["rows"], [[5]])
        self.assertEqual(result["model_calls"], 6)
        self.assertEqual(result["node_path"], ["orchestrator", "intent", "orchestrator", "workspace", "orchestrator", "schema", "orchestrator", "columns", "orchestrator",
            "sql", "orchestrator", "validation", "orchestrator", "execution", "orchestrator", "response", "orchestrator", "finalize"])

    def test_table_confirmation_precedes_generation_and_refreshes_schema(self):
        scripts=self.scripts()
        scripts["schema"]=[{"tables":["orders","customers"],"reason":"Possible customer grouping"}]
        app=self.workflow(scripts)
        def confirm(proposal):
            self.assertEqual(self.models["sql"].contexts,[])
            self.assertEqual(proposal["tables"],["customers","orders"])
            return ["orders"]
        result=app.ask("Count orders",confirm_tables=confirm)
        self.assertEqual(result["status"],"answered")
        self.assertEqual(result["tables"],["orders"])
        self.assertEqual([t["table"] for t in self.models["columns"].contexts[0]["schema"]["tables"]],["orders"])
        denied=self.workflow().ask("Count orders",confirm_tables=lambda proposal:["private"])
        self.assertEqual(denied["status"],"failed")
        self.assertEqual(denied["queries"],[])
        self.assertEqual(self.models["sql"].contexts,[])

    def test_domain_is_a_hint_and_can_propose_other_permitted_tables(self):
        from langgraph_impl.knowledge import KnowledgeBase
        knowledge=KnowledgeBase().with_domains([{"id":"stock_only","name":"Stock only","description":"Inventory balances",
            "tables":["inventory"],"keywords":["stock"],"revision":0}],self.db.allowed)
        app=self.workflow(workspaces=["stock_only"],domain_hints=True,knowledge=knowledge)
        proposals=[]
        result=app.ask("Count all orders",confirm_tables=lambda proposal:proposals.append(proposal) or proposal["tables"])
        self.assertEqual(result["status"],"answered")
        self.assertEqual(result["tables"],["orders"])
        self.assertIn("Additional tables outside",proposals[0]["reason"])
        self.assertIn("orders",proposals[0]["available_tables"])
        self.assertTrue(self.models["schema"].contexts[0]["catalog"]["domain_hint"]["domain_hints"])

    def test_clarification_and_refusal_stop(self):
        for status in ("clarification", "refused"):
            scripts = {"intent": [{**READY, "status": status, "message": "Specify your date range"}]}
            with self.subTest(status=status), patch.object(self.db, "execute") as execute:
                result = self.workflow(scripts).ask("Which date range?" if status=="clarification" else "Delete orders")
                self.assertEqual(result["status"], status)
                self.assertNotIn("schema", result["node_path"])
                execute.assert_not_called()

    def test_compile_repair(self):
        scripts = self.scripts(2)
        scripts["sql"][0] = {"sql": "SELECT missing FROM orders", "parameters": []}
        result = self.workflow(scripts).ask("Count orders")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["node_path"].count("execution"), 1)
        self.assertIn("missing", self.models["schema"].contexts[1]["retry_feedback"]["error"])

    def test_semantic_rejection_and_attempt_limit(self):
        for limit in (1, 2):
            scripts = self.scripts(2)
            scripts["validation"][0] = {"approved": False, "feedback": "Review filters"}
            with self.subTest(limit=limit), patch.object(self.db, "execute", wraps=self.db.execute) as execute:
                result = self.workflow(scripts, max_attempts=limit).ask("Count orders")
                self.assertEqual(result["status"], "failed" if limit == 1 else "answered")
                self.assertEqual(execute.call_count, limit - 1)

    def test_execution_retry_revalidates(self):
        execute = self.db.execute
        calls = 0

        def flaky(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise QueryError("interrupted")
            return execute(*args)

        with patch.object(self.db, "execute", side_effect=flaky):
            result = self.workflow(self.scripts(2)).ask("Count orders")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(len(self.models["validation"].contexts), 2)

    def test_unsafe_sql_and_wrong_scope(self):
        for sql in ("DELETE FROM orders", "SELECT * FROM sqlite_schema", "SELECT load_extension('x')",
                    "SELECT 1; SELECT 2", "WITH x AS (SELECT 1) DELETE FROM orders RETURNING order_id"):
            with self.subTest(sql=sql), self.assertRaises(QueryError):
                self.db.execute(sql, {}, self.db.allowed)
        with self.assertRaises(QueryError):
            self.db.execute("SELECT * FROM customers", {}, {"orders"})

    def test_approval_is_bound_and_consumed(self):
        registry = ApprovalRegistry()
        candidate = Candidate.parse(COUNT)
        changed = Candidate.parse({"sql": "SELECT order_id FROM orders", "parameters": []})
        for query, tables in ((changed, ["orders"]), (candidate, ["orders", "customers"])):
            with self.assertRaises(RuntimeError):
                registry.consume(registry.issue(candidate, ["orders"]), query, tables)
        approval = registry.issue(candidate, ["orders"])
        registry.consume(approval, candidate, ["orders"])
        with self.assertRaises(RuntimeError):
            registry.consume(approval, candidate, ["orders"])
        with patch("langgraph_impl.workflow.ValidationAgent.validate", return_value=ValidationResult(Approval("fake"), "ok")), patch.object(self.db, "execute") as execute:
            result = self.workflow().ask("Count orders")
            self.assertEqual(result["status"], "failed")
            execute.assert_not_called()

    def test_contracts_validate_locally_without_strict_provider(self):
        scripts = self.scripts()
        scripts["validation"] = [{"approved": "false", "feedback": "wrong type"},
                                 {"approved": True, "feedback": "correct type"}]
        result = self.workflow(scripts).ask("Count orders")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["model_calls"], 7)
        with self.assertRaises(ValidationError):
            Candidate.parse({"sql": "SELECT :x", "parameters": [{"name": "x", "value": True}]})

    def test_budget_and_response_failure(self):
        result = self.workflow(max_model_calls=1).ask("Count orders")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["model_calls"], 1)
        app = self.workflow()
        with patch.object(self.models["response"], "generate", side_effect=RuntimeError("provider offline")):
            result = app.ask("Count orders")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["queries"]), 1)

    def test_partial_results_and_timeout(self):
        scripts = self.scripts()
        scripts["sql"] = [{"sql": "SELECT order_id FROM orders", "parameters": []}]
        result = self.workflow(scripts, database=Database(self.path, row_limit=1)).ask("List orders")
        self.assertIn("Result limit", result["answer"])
        db = Database(self.path, timeout=0.01)
        with self.assertRaisesRegex(QueryError, "interrupted"):
            db.execute("WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT sum(n) FROM x", {}, set())

    def test_real_join_and_foreign_keys(self):
        result = self.db.execute("""SELECT c.name, SUM(p.amount_cents)/100.0 FROM customers c
            JOIN orders o ON o.customer_id=c.customer_id JOIN payments p ON p.order_id=o.order_id
            WHERE p.status=:status GROUP BY c.customer_id ORDER BY c.customer_id""",
            {"status": "succeeded"}, self.db.allowed)
        self.assertEqual(result["rows"], [["Mira", 150.0], ["Arjun", 150.0], ["Leena", 200.0]])
        conn = sqlite3.connect(self.path)
        try:
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()
        self.assertEqual(len(self.db.allowed), 10)
        relationship = self.db.schema(["orders"])["tables"][0]["foreign_keys"][0]
        self.assertEqual(relationship["references_column"], "customer_id")

    def test_schema_agent_receives_only_permitted_metadata(self):
        limited = Database(self.path, allowed_tables=["orders"])
        model = FixtureModel([{"tables": ["orders"], "reason": "Count rows"}])
        selected = SchemaAgent(limited, model, Budget()).select("Count orders", READY, None)
        metadata = model.contexts[0]["catalog"]["metadata"]
        self.assertEqual([table["table"] for table in metadata], ["orders"])
        self.assertIn("customer_id", metadata[0]["columns"])
        self.assertEqual(metadata[0]["foreign_keys"], [])
        self.assertEqual(selected["selected_tables"], ["orders"])

    def test_new_requests_and_diagram_are_isolated(self):
        scripts = self.scripts()
        app = LangGraphWorkflow(self.db, model_factory=lambda role: FixtureModel(scripts[role]))
        self.assertEqual(app.ask("Count orders"), app.ask("Count orders"))
        self.assertIn("orchestrator", app.build_graph().get_graph().draw_mermaid())

    def test_no_legacy_imports_in_any_source(self):
        permitted = set(sys.stdlib_module_names) | {
            "langgraph_impl", "langgraph", "langchain_core", "langchain_openai", "dotenv", "openai", "pydantic", "fastapi", "starlette", "sqlalchemy", "sqlglot", "cryptography", "google", "oracledb", "requests", "httpx", "plotly", "matplotlib", "seaborn", "certifi", "snowflake", "pytds", "OpenSSL"}
        for source in APP.rglob("*.py"):
            if ".venv" in source.parts or "build" in source.parts:
                continue
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for item in ast.walk(tree):
                names = [n.name for n in item.names] if isinstance(item, ast.Import) else [item.module or ""] if isinstance(item, ast.ImportFrom) else []
                self.assertTrue(all(not n or n.split(".")[0] in permitted or
                    (isinstance(item, ast.ImportFrom) and item.level > 0) for n in names), str(source))

    def test_runs_after_relocation_without_parent_project(self):
        relocated = Path(self.temp.name) / "isolated" / "langgraph_impl"
        relocated.mkdir(parents=True)
        for source in APP.glob("*.py"):
            shutil.copy2(source, relocated / source.name)
        for source in APP.glob("*.sql"):
            shutil.copy2(source, relocated / source.name)
        shutil.copytree(APP/"knowledge",relocated/"knowledge")
        program = '''from langgraph_impl.demo import create_demo,DEFAULT_DATABASE
from langgraph_impl.storage import Database
from langgraph_impl.workflow import LangGraphWorkflow
from pathlib import Path
import langgraph_impl
assert Path(langgraph_impl.__file__).resolve().parent == Path.cwd()/"langgraph_impl"
create_demo()
db=Database(DEFAULT_DATABASE)
assert db.execute("SELECT COUNT(*) FROM orders",{},db.allowed)["rows"]==[[25]]
assert "orchestrator" in LangGraphWorkflow(db).build_graph().get_graph().draw_mermaid()
class OfflineModel:
 def generate(self,instructions,context,output_type):
  if output_type.__name__=="ColumnSelection": return {"tables":[{"table":t["table"],"columns":[]} for t in context["schema"]["tables"]],"reason":"Keys retained"}
  return {"IntentOutput":{"status":"ready","task":"Count orders","assumptions":[],"message":""},
   "SchemaSelection":{"tables":["orders"],"reason":"Count rows"},
   "SQLSubmission":{"sql":"SELECT COUNT(*) FROM orders","parameters":[]},
   "ReviewOutput":{"approved":True,"feedback":"Correct"},
   "AnswerOutput":{"answer":"25 orders","reason":"Counted all order records."}}[output_type.__name__]
answer=LangGraphWorkflow(db,model_factory=lambda role:OfflineModel()).ask("How many orders?")
assert answer["status"]=="answered" and answer["queries"][0]["result"]["rows"]==[[25]]
print("Standalone relocation passed")
'''
        proc = subprocess.run([sys.executable, "-c", program], cwd=relocated.parent,
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Standalone relocation passed", proc.stdout)


class LocalConfigAndModelTests(unittest.TestCase):
    def test_env_is_local_and_generic_settings_override_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("GROQ_API_KEY=test-secret\nGROQ_MODEL=openai/gpt-oss-20b\n", encoding="utf-8")
            with patch.object(config, "APP_DIR", Path(directory)):
                settings = ModelConfig.load(environ={})
            self.assertEqual(settings.base_url, "https://api.groq.com/openai/v1")
            self.assertNotIn("test-secret", repr(settings))
            settings = ModelConfig.load(env_file=path, environ={"LLM_MODEL": "other"})
            self.assertEqual(settings.model, "other")

    def test_endpoint_normalization_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            local_file = Path(directory) / ".env"
            local_file.write_text("", encoding="utf-8")
            for style, suffix in (("responses", "responses"), ("chat_completions", "chat/completions")):
                settings = ModelConfig.load(
                    environ={"LLM_ENDPOINT": "https://api.groq.com/openai/v1/" + suffix,
                             "LLM_API_STYLE": style, "LLM_MODEL": "test", "LLM_API_KEY": "test"}, env_file=local_file)
                self.assertEqual(settings.base_url, "https://api.groq.com/openai/v1")
                self.assertEqual(settings.api_style, style)
            with self.assertRaises(ValueError):
                ModelConfig.load(env_file=Path(directory) / "missing.env", environ={})
            with patch.object(config, "APP_DIR", Path(directory)), self.assertRaises(ValueError):
                ModelConfig.load(environ={})

    def test_langchain_binding_and_wrong_tool_rejection(self):
        captured = {}

        class FakeChat:
            def bind_tools(self, tools, **options):
                captured.update(options)
                return self

            def invoke(self, messages):
                return AIMessage(content="", tool_calls=[{"id": "1", "name": "IntentOutput", "args": READY}])

        settings = ModelConfig(model="test", api_key="test-key")
        from langgraph_impl.tests.auth_helpers import approved_model_policy
        model = LangChainModel(config=settings, chat=FakeChat(),egress_policy=approved_model_policy())
        self.assertEqual(model.generate("role", {"question": "Count orders"}, IntentOutput), READY)
        self.assertEqual(captured["tool_choice"], "IntentOutput")
        self.assertFalse(captured["parallel_tool_calls"])
        with patch.object(model.chat, "invoke", return_value=AIMessage(content="", tool_calls=[
                {"id": "2", "name": "execute_sql", "args": COUNT}])):
            with self.assertRaises(ValueError):
                model.generate("role", {}, IntentOutput)

    def test_provider_generated_tool_error_is_retryable(self):
        class InvalidToolChat:
            def bind_tools(self, *args, **kwargs):
                return self

            def invoke(self, *args):
                response = SimpleNamespace(status_code=400, request=SimpleNamespace(), headers={})
                raise BadRequestError("invalid generation", response=response,
                                      body={"code": "tool_use_failed", "message": "private provider details"})

        from langgraph_impl.tests.auth_helpers import approved_model_policy
        model = LangChainModel(config=ModelConfig(model="test", api_key="test"), chat=InvalidToolChat(),egress_policy=approved_model_policy())
        with self.assertRaisesRegex(ValueError, "invalid tool handoff") as error:
            model.generate("role", {}, IntentOutput)
        self.assertNotIn("private provider details", str(error.exception))


if __name__ == "__main__":
    unittest.main()
