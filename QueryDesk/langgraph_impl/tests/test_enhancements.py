import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database
from langgraph_impl.knowledge import KnowledgeBase, DATA_DIR
from langgraph_impl.pruning import apply_pruning, recover_required, prompt_schema
from langgraph_impl.evaluation import Evaluator, equivalent_rows, matches_reference_sql
from langgraph_impl.workflow import LangGraphWorkflow


class EnhancementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"sales.db"
        create_demo(self.path)
        self.db=Database(self.path)
        self.knowledge=KnowledgeBase()

    def test_all_golden_references_match_stored_answers(self):
        report=Evaluator(self.db).run(reference_only=True)
        self.assertEqual(report["case_count"],30)
        self.assertTrue(report["references_verified"])

    def test_heldout_references_match_and_cover_high_risk_question_types(self):
        report=Evaluator(self.db,suite="heldout").run(reference_only=True)
        self.assertEqual(report["case_count"],37)
        cases=Evaluator(self.db,suite="heldout").cases
        tags={tag for case in cases for tag in case["tags"]}
        self.assertTrue({"ambiguous_metric","time_boundary","outer_join","fanout","business_critical"}<=tags)

    def test_reviewed_examples_and_metric_columns_are_real(self):
        schema={t["table"]:{c["name"] for c in t["columns"]} for t in self.db.schema(sorted(self.db.allowed))["tables"]}
        for example in self.knowledge.examples:
            self.db.validate(example["sql"],example["parameters"],example["tables"])
        for metric in self.knowledge.catalog["metrics"]:
            for table,columns in metric["columns"].items():
                self.assertTrue(set(columns)<=schema[table],metric["id"])

    def test_workspace_routing_scopes_and_fallback(self):
        route=self.knowledge.route("Which warehouses have low stock?",self.db.allowed)
        self.assertEqual(route["workspaces"],["inventory"])
        self.assertNotIn("payments",route["tables"])
        route=self.knowledge.route("stock and payments",self.db.allowed)
        self.assertIn("inventory",route["workspaces"])
        self.assertIn("payments",route["workspaces"])
        narrow=self.knowledge.route("Count orders",{"orders"},requested=["sales"])
        self.assertEqual(narrow["tables"],["orders"])
        self.assertEqual(len(self.knowledge.route("xyz",self.db.allowed)["workspaces"]),3)
        with self.assertRaises(ValueError):
            self.knowledge.route("",self.db.allowed,requested=["unknown"])

    def test_ambiguous_metrics_stop_without_model_calls(self):
        workflow=LangGraphWorkflow(self.db,model_factory=lambda role:self.fail("Model must not be called"))
        for question in ("What is revenue?","What is profit and how many orders?"):
            result=workflow.ask(question)
            self.assertEqual(result["status"],"clarification")
            self.assertEqual(result["model_calls"],0)
        self.assertIsNone(self.knowledge.clarification("net collected cash"))

    def test_example_retrieval_relevance_scope_and_staleness(self):
        examples=self.knowledge.retrieve("net collected cash",self.db,["payments","refunds"])
        self.assertTrue(examples)
        self.assertEqual(examples[0]["id"],"payments_net_cash")
        self.assertTrue(all(set(e["tables"])<={"payments","refunds"} for e in examples))
        self.assertEqual(self.knowledge.retrieve("customer payments",self.db,["payments"]),[])
        self.knowledge.examples=[{"id":"stale","question":"net collected cash","sql":"SELECT missing FROM payments",
                                  "parameters":{},"tables":["payments"],"tags":[]}]
        self.assertEqual(self.knowledge.retrieve("net cash",self.db,["payments"]),[])

    def test_pruning_preserves_keys_metric_columns_and_recovers_group_filter(self):
        tables=["orders","customers","order_items"]
        info={"selected_tables":tables,"schema":self.db.schema(tables)}
        original=deepcopy(info)
        proposal={"tables":[{"table":t,"columns":[]} for t in tables],"reason":"minimal"}
        result=apply_pruning(info,proposal,self.knowledge.context("gross order value"),[])
        kept={t["table"]:{c["name"] for c in t["columns"]} for t in result["schema"]["tables"]}
        self.assertTrue({"order_id","customer_id","status","currency"}<=kept["orders"])
        self.assertTrue({"order_id","product_id","quantity","unit_price_cents"}<=kept["order_items"])
        self.assertNotIn("region",kept["customers"])
        self.assertEqual(info,original)
        plan=self.db.validate("""SELECT c.region,COUNT(*) FROM orders o JOIN customers c USING(customer_id)
            WHERE c.name=:name GROUP BY c.region ORDER BY c.region""",{"name":"Mira"},tables)
        recovered=recover_required(result,plan["referenced_columns"])
        self.assertEqual(set(recovered["pruning"]["recovered_columns"]["customers"]),{"name","region"})
        self.assertNotIn("full_schema",prompt_schema(recovered))
        with self.assertRaises(ValueError):
            apply_pruning(info,{"tables":[{"table":"orders","columns":["missing"]}],"reason":"bad"},
                          self.knowledge.context("Count orders"),[])

    def test_example_columns_cannot_be_pruned(self):
        info={"selected_tables":["payments","refunds"],"schema":self.db.schema(["payments","refunds"])}
        examples=self.knowledge.retrieve("monthly net cash",self.db,info["selected_tables"])
        result=apply_pruning(info,{"tables":[{"table":t,"columns":[]} for t in info["selected_tables"]],"reason":"minimal"},
                             {"metrics":[]},examples)
        cols={t["table"]:{c["name"] for c in t["columns"]} for t in result["schema"]["tables"]}
        self.assertIn("paid_at",cols["payments"])
        self.assertIn("refunded_at",cols["refunds"])

    def test_result_comparison_duplicates_nulls_order_and_numeric_tolerance(self):
        self.assertTrue(equivalent_rows([[1.0000001],[None],[2]],[[2],[1],[None]]))
        self.assertFalse(equivalent_rows([[1],[1]],[[1],[2]]))
        self.assertFalse(equivalent_rows([[None]],[[0]]))
        self.assertFalse(equivalent_rows([[1],[2]],[[2],[1]],ordered=True))
        self.assertFalse(equivalent_rows([[1,2]],[[1]]))

    def test_sql_reference_match_is_reported_separately_from_result_correctness(self):
        self.assertTrue(matches_reference_sql(
            "select count ( * ) from orders;", "SELECT COUNT(*) FROM orders", {}, {}))
        self.assertFalse(matches_reference_sql(
            "SELECT COUNT(*) FROM orders WHERE status='completed'",
            "SELECT COUNT(*) FROM orders", {}, {}))
        self.assertFalse(matches_reference_sql(
            "SELECT COUNT(*) FROM orders; SELECT 1",
            "SELECT COUNT(*) FROM orders", {}, {}))
        self.assertFalse(matches_reference_sql(
            "SELECT COUNT(*) FROM orders WHERE status=:status",
            "SELECT COUNT(*) FROM orders WHERE status=:status",
            {"status":"pending"},{"status":"completed"}))

    def test_repeated_evaluation_reports_wrong_results_and_provider_failures(self):
        count=self.db.execute("SELECT COUNT(*) FROM orders",{},self.db.allowed)
        values=iter([
            {"status":"answered","answer":"25","queries":[{"sql":"select count(*) from orders","parameters":{},"result":count}]},
            {"status":"answered","answer":"incorrect","queries":[{"sql":"SELECT COUNT(*) FROM orders WHERE status='completed'","parameters":{},"result":{**count,"rows":[[24]]}}]},
            {"status":"failed","answer":"HTTP 429","queries":[{"sql":"SELECT COUNT(*) FROM orders","parameters":{},"result":count}]},
        ])
        questions=[]
        class Workflow:
            def ask(self,question):
                questions.append(question)
                return next(values)
        report=Evaluator(self.db,Workflow).run(repeats=3,case_ids=["order_count"],max_retries=0)
        self.assertEqual(report["summary"]["passed"],1)
        self.assertEqual(report["summary"]["correct_executions"],2)
        self.assertEqual(report["summary"]["sql_reference_matches"],2)
        self.assertEqual(report["summary"]["result_correct_runs"],2)
        self.assertEqual(report["runs"][1]["sql_reference_match"],False)
        self.assertFalse(report["runs"][1]["result_correct"])
        self.assertEqual(report["summary"]["rate_limited_runs"],1)
        self.assertFalse(report["summary"]["per_case"]["order_count"]["stable_pass"])
        self.assertTrue(all(isinstance(q,str) and "SELECT" not in q for q in questions))

    def test_stale_reference_fails_instead_of_relabeling_expected_answers(self):
        golden=json.loads((DATA_DIR/"golden.json").read_text(encoding="utf-8"))
        golden[0]["expected_rows"]=[[999]]
        path=Path(self.temp.name)/"bad.json"
        path.write_text(json.dumps(golden),encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"does not match"):
            Evaluator(self.db,golden_file=path).run(reference_only=True)

    def test_repeated_full_graph_with_business_examples_and_pruning(self):
        contexts=[]
        class Model:
            def generate(self,instructions,context,output_type):
                contexts.append((output_type.__name__,context))
                if output_type.__name__=="ColumnSelection":
                    return {"tables":[{"table":t["table"],"columns":["amount_cents"]}
                        for t in context["schema"]["tables"]],"reason":"Only cash amounts requested"}
                return {
                    "IntentOutput":{"status":"ready","task":"Net collected cash","assumptions":[],"message":""},
                    "SchemaSelection":{"tables":["payments","refunds"],"reason":"Independent cash streams"},
                    "SQLSubmission":{"sql":"""SELECT (SELECT SUM(amount_cents) FROM payments WHERE status=:status)/100.0
                        -(SELECT SUM(amount_cents) FROM refunds WHERE status=:status)/100.0""",
                        "parameters":[{"name":"status","value":"succeeded"}]},
                    "ReviewOutput":{"approved":True,"feedback":"Correct metric and no join fan-out"},
                    "AnswerOutput":{"answer":"Net collected cash is INR 7495.","reason":"Succeeded payments minus succeeded refunds, divided by 100."},
                }[output_type.__name__]
        factory=lambda:LangGraphWorkflow(self.db,model_factory=lambda role:Model())
        report=Evaluator(self.db,factory).run(repeats=2,case_ids=["net_cash"])
        self.assertEqual(report["summary"]["passed"],2)
        self.assertTrue(report["summary"]["per_case"]["net_cash"]["stable_pass"])
        for run in report["runs"]:
            self.assertEqual(run["workspaces"],["payments"])
            self.assertIn("payments_net_cash",run["example_ids"])
            self.assertLess(run["pruning"]["prompt_columns"],run["pruning"]["original_columns"])
            self.assertEqual(run["model_calls"],6)
        sql_context=next(context for name,context in contexts if name=="SQLSubmission")
        self.assertNotIn("full_schema",sql_context["schema"])
        self.assertIn("net_collected_cash",[m["id"] for m in sql_context["business_definitions"]["metrics"]])
        self.assertNotIn("expected_rows",sql_context)


if __name__=="__main__":
    unittest.main()
