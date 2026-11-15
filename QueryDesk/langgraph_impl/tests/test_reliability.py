import json
import tempfile
import unittest
from pathlib import Path

from langgraph_impl.demo import create_demo
from langgraph_impl.storage import Database
from langgraph_impl.evaluation import Evaluator
from langgraph_impl.provider_errors import ProviderError,retry_delay
from langgraph_impl.knowledge import DATA_DIR,KnowledgeBase
from langgraph_impl.checkpoint import report_lock


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        create_demo(self.path/"sales.db")
        self.db=Database(self.path/"sales.db")
        self.good={"status":"answered","answer":"25","queries":[{"result":self.db.execute("SELECT COUNT(*) FROM orders",{},["orders"])}]}

    def factory(self,values,questions):
        iterator=iter(values)
        class Workflow:
            def ask(inner,question):
                questions.append(question)
                value=next(iterator)
                if isinstance(value,BaseException):
                    raise value
                return value
        return Workflow

    def test_interruption_resume_and_configuration_guard(self):
        path=self.path/"evaluation.json"
        questions=[]
        config={"model":"fixture","api_key":"do-not-save"}
        evaluator=Evaluator(self.db,self.factory([self.good,KeyboardInterrupt()],questions),configuration=config)
        with self.assertRaises(KeyboardInterrupt):
            evaluator.run(repeats=2,case_ids=["order_count"],report_path=path)
        saved=json.loads(path.read_text())
        self.assertEqual(len(saved["runs"]),1)
        self.assertNotIn("do-not-save",path.read_text())
        for run in saved["runs"]:
            run.pop("result_correct",None)
            run.pop("sql_reference_match",None)
        path.write_text(json.dumps(saved),encoding="utf-8")
        resumed=Evaluator(self.db,self.factory([self.good],questions),configuration=config).run(
            repeats=2,case_ids=["order_count"],report_path=path,resume=True)
        self.assertEqual(resumed["summary"]["passed"],2)
        self.assertIn("sql_reference_match",resumed["runs"][0])
        self.assertIn("result_correct",resumed["runs"][0])
        self.assertEqual(len(questions),3) # one interrupted call, only the unfinished trial is repeated
        self.assertTrue(resumed["completed"])
        with self.assertRaisesRegex(ValueError,"settings"):
            Evaluator(self.db,configuration={"model":"changed"}).run(repeats=2,case_ids=["order_count"],report_path=path,resume=True)
        with self.assertRaisesRegex(ValueError,"already exists"):
            evaluator.run(repeats=2,case_ids=["order_count"],report_path=path)

    def test_transient_recovery_and_long_retry_guidance(self):
        delays=[]
        questions=[]
        evaluator=Evaluator(self.db,self.factory([ProviderError("HTTP 429","rate_limit",3),self.good],questions))
        report=evaluator.run(repeats=1,case_ids=["order_count"],sleep=delays.append)
        self.assertEqual(delays,[3])
        self.assertEqual(report["summary"]["passed"],1)
        self.assertEqual(report["summary"]["retries"],1)
        evaluator=Evaluator(self.db,self.factory([ProviderError("HTTP 429","rate_limit",900)],[]))
        report=evaluator.run(repeats=1,case_ids=["order_count"],sleep=delays.append)
        self.assertEqual(report["summary"]["total_runs"],1)
        self.assertEqual(report["runs"][0]["failure_kind"],"rate_limit")
        self.assertEqual(delays,[3])

    def test_retry_journal_survives_interruption_without_resetting_budget(self):
        path=self.path/"retry.json"
        def interrupt(_):
            raise KeyboardInterrupt()
        evaluator=Evaluator(self.db,self.factory([ProviderError("HTTP 429","rate_limit",0)],[]))
        with self.assertRaises(KeyboardInterrupt):
            evaluator.run(repeats=1,case_ids=["order_count"],report_path=path,sleep=interrupt,max_retries=1)
        resumed=Evaluator(self.db,self.factory([ProviderError("HTTP 429","rate_limit",0)],[])).run(
            repeats=1,case_ids=["order_count"],report_path=path,resume=True,max_retries=1)
        self.assertEqual(len(resumed["runs"][0]["attempts"]),2)
        self.assertEqual(resumed["summary"]["passed"],0)

    def test_report_lock_and_provider_guidance(self):
        with report_lock(self.path/"report.json"):
            with self.assertRaisesRegex(ValueError,"already in use"):
                with report_lock(self.path/"report.json"):
                    pass
        self.assertEqual(retry_delay({"retry-after":"4"}),4)
        self.assertEqual(retry_delay({"retry-after-ms":"2500"}),2.5)
        self.assertIsNone(retry_delay({"retry-after":"invalid"}))

    def test_heldout_references_and_generation_isolation(self):
        heldout=Evaluator(self.db,suite="heldout")
        self.assertEqual(heldout.run(reference_only=True)["case_count"],37)
        examples=KnowledgeBase().examples
        self.assertFalse({e["question"] for e in examples}&{c["question"] for c in heldout.cases})
        self.assertTrue(all(c.get("domains") and c.get("tags") and c.get("difficulty") and c.get("rubric") for c in heldout.cases))
        context=json.dumps(KnowledgeBase().context("cash and order count",self.db.allowed))
        self.assertNotIn("expected_rows",context)
        self.assertNotIn("heldout_",context)
