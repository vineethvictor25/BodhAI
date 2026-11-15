"""Repeated result-based regression evaluation; reference answers never enter prompts."""
import json
import math
import time
import hashlib
import statistics
from decimal import Decimal
import sqlglot
from sqlglot.errors import ParseError
from .value_codec import loads as exact_loads,dumps as exact_dumps,decode
from contextlib import nullcontext
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .knowledge import DATA_DIR
from .checkpoint import report_lock, save_report


def equivalent_rows(actual, expected, ordered=False, tolerance=1e-6):
    actual,expected=decode(actual),decode(expected)
    def cell(a,b):
        if a is None or b is None:
            return a is b
        if type(a) is int and type(b) is int:return a==b
        if isinstance(a,Decimal) or isinstance(b,Decimal):
            if type(a) not in (int,float,Decimal) or type(b) not in (int,float,Decimal):return False
            # Decimal comparisons are exact; explicit approximate float cases retain their old path.
            return Decimal(str(a))==Decimal(str(b))
        if type(a) in (int,float) and type(b) in (int,float):
            return math.isclose(a,b,rel_tol=0,abs_tol=tolerance)
        return type(a) is type(b) and a == b

    def row(a,b):
        return len(a)==len(b) and all(cell(x,y) for x,y in zip(a,b))

    if len(actual)!=len(expected):
        return False
    if ordered:
        return all(row(a,b) for a,b in zip(actual,expected))
    # Bipartite matching preserves duplicate counts and handles tolerance overlaps.
    assigned = {}

    def match(i,seen):
        for j,target in enumerate(expected):
            if j not in seen and row(actual[i],target):
                seen.add(j)
                if j not in assigned or match(assigned[j],seen):
                    assigned[j]=i
                    return True
        return False

    return all(match(i,set()) for i in range(len(actual)))


def matches_reference_sql(actual, expected, actual_parameters, expected_parameters):
    if not isinstance(actual,str) or not isinstance(expected,str):
        return False
    try:
        actual_parts=sqlglot.parse(actual,read="sqlite")
        expected_parts=sqlglot.parse(expected,read="sqlite")
        if len(actual_parts)!=1 or len(expected_parts)!=1 or actual_parts[0] is None or expected_parts[0] is None:
            return False
        actual_sql=actual_parts[0].sql(dialect="sqlite",normalize=True)
        expected_sql=expected_parts[0].sql(dialect="sqlite",normalize=True)
    except ParseError:
        return False
    return actual_sql==expected_sql and exact_dumps(actual_parameters,sort_keys=True)==exact_dumps(expected_parameters,sort_keys=True)


class Evaluator:
    def __init__(self, database, workflow_factory=None, golden_file=None, suite="development", configuration=None):
        self.database,self.workflow_factory = database,workflow_factory
        if suite not in {"development","heldout"}:
            raise ValueError("Unknown evaluation suite")
        self.suite=suite
        # Explicit allowlist: provider credentials must never enter saved reports.
        self.configuration={key:value for key,value in (configuration or {}).items() if key in
            {"model","base_url","api_style","max_tokens","timeout","strict_tools","send_parallel","max_attempts","max_model_calls","workspaces"}}
        if (configuration or {}).get('agent_models') is not None:
            self.configuration['agent_models']={role:{key:value for key,value in settings.items() if key in
                {'model','base_url','api_style','max_tokens','timeout','strict_tools','send_parallel'}}
                for role,settings in configuration['agent_models'].items() if role in {'intent','schema','columns','sql','validation','response'}}
        self.cases = json.loads(Path(golden_file or DATA_DIR/("heldout.json" if suite=="heldout" else "golden.json")).read_text(encoding="utf-8"))
        if len({case["id"] for case in self.cases})!=len(self.cases):
            raise ValueError("Evaluation case IDs must be unique")

    def select(self, case_ids=None, limit=None):
        unknown = set(case_ids or [])-{case["id"] for case in self.cases}
        if unknown:
            raise ValueError("Unknown golden case: "+", ".join(sorted(unknown)))
        selected = [case for case in self.cases if not case_ids or case["id"] in case_ids]
        if limit is not None:
            if limit < 1:
                raise ValueError("Evaluation limit must be positive")
            selected = selected[:limit]
        return selected

    def verify_references(self,cases):
        for case in cases:
            if case["expected_status"] != "answered":
                continue
            rows = self.database.execute(case["reference_sql"],case["parameters"],case["tables"])
            if rows["truncated"] or not equivalent_rows(rows["rows"],case["expected_rows"],case["ordered"],case["absolute_tolerance"]):
                raise ValueError("Golden reference does not match this database: "+case["id"])

    def run(self, repeats=3, case_ids=None, limit=None, reference_only=False, pause_seconds=0, progress=None,
            report_path=None,resume=False,max_retries=2,max_retry_delay=60,sleep=time.sleep):
        if resume and not report_path:
            raise ValueError("Resume requires a report path")
        with report_lock(report_path) if report_path else nullcontext():
            return self._run(repeats,case_ids,limit,reference_only,pause_seconds,progress,
                report_path,resume,max_retries,max_retry_delay,sleep)

    def _run(self,repeats,case_ids,limit,reference_only,pause_seconds,progress,
            report_path,resume,max_retries,max_retry_delay,sleep):
        if not 1 <= repeats <= 20:
            raise ValueError("Evaluation repeats must be between 1 and 20")
        if not math.isfinite(pause_seconds) or not 0 <= pause_seconds <= 3600:
            raise ValueError("Evaluation pause must be between 0 and 3600 seconds")
        if not 0 <= max_retries <= 5 or not 0 <= max_retry_delay <= 60:
            raise ValueError("Use 0–5 retries and a retry delay cap of 0–60 seconds")
        cases = self.select(case_ids,limit)
        self.verify_references(cases)
        identity={"cases":cases,"repeats":repeats,"reference_only":reference_only,"suite":self.suite,
            "configuration":self.configuration,"database_sha256":hashlib.sha256(self.database.filename.read_bytes()).hexdigest(),
            "allowed_tables":sorted(self.database.allowed),"row_limit":self.database.row_limit,
            "max_retries":max_retries,"max_retry_delay":max_retry_delay,
            "context_sha256":hashlib.sha256((DATA_DIR/"catalog.json").read_bytes()+(DATA_DIR/"examples.json").read_bytes()).hexdigest(),
            "comparison_version":2}
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        report = {"created_utc":datetime.now(timezone.utc).isoformat(),"case_count":len(cases),
            "version":2,"fingerprint":fingerprint,"configuration":self.configuration,"suite":self.suite,
            "database_sha256":identity["database_sha256"],"completed":False,
            "reference_only":reference_only,"references_verified":True,"repeats":0 if reference_only else repeats,
            "pause_seconds":pause_seconds,
            "comparison":"Executed rows, positional columns, numeric tolerance, duplicates, NULLs and optional row order; aliases/prose are not scored.",
            "dataset":"Held-out authored questions excluded from generation context; sample-data benchmark only." if self.suite=="heldout" else "Development regression set; example patterns overlap. This is not an unseen-production accuracy benchmark.",
            "runs":[],"summary":{}}
        if resume:
            report=exact_loads(Path(report_path).read_text(encoding="utf-8"))
            if report.get("fingerprint")!=fingerprint:
                raise ValueError("Cannot resume: dataset, cases, settings or retry policy changed")
            case_by_id={case["id"]:case for case in cases}
            for run in report.get("runs",[]):
                case=case_by_id[run["case_id"]]
                run.setdefault("result_correct",run.get("execution_correct"))
                if "sql_reference_match" not in run:
                    queries=run.get("queries",[])
                    run["sql_reference_match"]=matches_reference_sql(
                        queries[-1].get("sql"),case["reference_sql"],
                        queries[-1].get("parameters",{}),case["parameters"]
                    ) if case["expected_status"]=="answered" and queries else (
                        False if case["expected_status"]=="answered" else None)
        elif report_path and Path(report_path).exists():
            raise ValueError("Report already exists; use --resume or choose a new report path")
        def checkpoint():
            report["updated_utc"]=datetime.now(timezone.utc).isoformat()
            if report_path:
                save_report(report_path,report)
        checkpoint()
        if reference_only:
            report["completed"]=True
            checkpoint()
            return report
        if self.workflow_factory is None:
            raise ValueError("A workflow factory is required for generated-query evaluation")
        for case in cases:
            for iteration in range(1,repeats+1):
                if any(run["case_id"]==case["id"] and run["iteration"]==iteration for run in report["runs"]):
                    continue
                if report["runs"] and pause_seconds:
                    # Keep individual waits bounded so interruption remains responsive.
                    remaining=pause_seconds
                    while remaining>0:
                        sleep(min(remaining,60)); remaining-=min(remaining,60)
                start=time.monotonic()
                attempts=[]
                pending=report.get("pending_trial",{})
                if pending.get("case_id")==case["id"] and pending.get("iteration")==iteration:
                    attempts=list(pending["attempts"])
                    result=pending["result"]
                    kind=attempts[-1]["error_kind"]
                    remaining=max(0,pending.get("retry_not_before",0)-time.time())
                    if remaining:
                        sleep(min(remaining,max_retry_delay))
                next_attempt=len(attempts) if not attempts or attempts[-1]["retry_delay"] is not None else max_retries+1
                for attempt in range(next_attempt,max_retries+1):
                    attempt_start=time.monotonic()
                    try:
                        result=self.workflow_factory().ask(case["question"])
                    except (RuntimeError,ValueError,OSError) as exc:
                        result={"status":"failed","answer":str(exc),"queries":[],
                            "error_info":exc.details() if hasattr(exc,"details") else {}}
                    error=result.get("error_info",{})
                    kind=error.get("kind")
                    # Compatibility with older workflow adapters; do not retry semantic failures.
                    if not kind and "HTTP 429" in result.get("answer",""):
                        kind="rate_limit"
                    delay=error.get("retry_after")
                    delay=2**attempt if delay is None else delay
                    retryable=result.get("status")=="failed" and kind in {"rate_limit","transient","connection"}
                    retry=retryable and attempt<max_retries and isinstance(delay,(int,float)) and math.isfinite(delay) and 0<=delay<=max_retry_delay
                    attempts.append({"number":attempt+1,"status":result.get("status"),"error_kind":kind,
                        "retry_after":error.get("retry_after"),"retry_delay":delay if retry else None,
                        "elapsed_seconds":round(time.monotonic()-attempt_start,3),"model_calls":result.get("model_calls",0)})
                    # Journal the pending trial too, before any wait or retry.
                    safe_result={key:value for key,value in result.items() if key in {"status","answer","queries","model_calls","workspaces","example_ids","pruning","error_info"}}
                    report["pending_trial"]={"case_id":case["id"],"iteration":iteration,"attempts":attempts,
                        "result":safe_result,"retry_not_before":time.time()+delay if retry else 0}
                    checkpoint()
                    if not retry:
                        break
                    sleep(delay)
                queries=result.get("queries",[])
                rows=queries[-1]["result"] if queries else None
                execution_correct = bool(rows is not None and not rows["truncated"] and
                    equivalent_rows(rows["rows"],case["expected_rows"],case["ordered"],case["absolute_tolerance"])) if case["expected_status"]=="answered" else None
                sql_reference_match = matches_reference_sql(
                    queries[-1].get("sql"),case["reference_sql"],
                    queries[-1].get("parameters",{}),case["parameters"]) if case["expected_status"]=="answered" and queries else (
                        False if case["expected_status"]=="answered" else None)
                passed = result["status"]==case["expected_status"] and (
                    execution_correct if case["expected_status"]=="answered" else not queries)
                report["runs"].append({"case_id":case["id"],"iteration":iteration,"passed":bool(passed),
                    "execution_correct":execution_correct,"result_correct":execution_correct,
                    "sql_reference_match":sql_reference_match,"expected_status":case["expected_status"],"status":result["status"],
                    "elapsed_seconds":round(time.monotonic()-start,3),"model_calls":result.get("model_calls",0),
                    "attempts":attempts,"failure_kind":kind if result["status"]=="failed" else None,
                    "answer":result.get("answer",""),"queries":queries,"expected_rows":case["expected_rows"],
                    "workspaces":result.get("workspaces",[]),"example_ids":result.get("example_ids",[]),
                    "pruning":result.get("pruning",{})})
                report.pop("pending_trial",None)
                checkpoint()
                if progress:
                    progress(report["runs"][-1])
        runs=report["runs"]
        statuses=Counter(r["status"] for r in runs)
        sql_runs=[r for r in runs if r["sql_reference_match"] is not None]
        result_runs=[r for r in runs if r["result_correct"] is not None]
        report["summary"]={"total_runs":len(runs),"passed":sum(r["passed"] for r in runs),
            "pass_rate":sum(r["passed"] for r in runs)/len(runs) if runs else 0,
            "status_counts":dict(statuses),"correct_executions":sum(r["execution_correct"] is True for r in runs),
            "sql_reference_matches":sum(r["sql_reference_match"] is True for r in sql_runs),
            "sql_reference_match_rate":sum(r["sql_reference_match"] is True for r in sql_runs)/len(sql_runs) if sql_runs else 0,
            "result_correct_runs":sum(r["result_correct"] is True for r in result_runs),
            "result_correct_rate":sum(r["result_correct"] is True for r in result_runs)/len(result_runs) if result_runs else 0,
            "rate_limited_runs":sum(any(a["error_kind"]=="rate_limit" for a in r.get("attempts",[])) for r in runs),
            "per_case":{case["id"]:{"passed":sum(r["passed"] for r in runs if r["case_id"]==case["id"]),
                "runs":repeats,"stable_pass":all(r["passed"] for r in runs if r["case_id"]==case["id"])} for case in cases}}
        latencies=sorted(r["elapsed_seconds"] for r in runs)
        report["summary"].update(failure_counts=dict(Counter(r.get("failure_kind") or ("incorrect_result" if not r["passed"] else "none") for r in runs)),
            latency_median=statistics.median(latencies) if latencies else 0,
            latency_p95=latencies[max(0,math.ceil(.95*len(latencies))-1)] if latencies else 0,
            retries=sum(max(0,len(r.get("attempts",[]))-1) for r in runs),
            stable_pass_cases=sum(value["stable_pass"] for value in report["summary"]["per_case"].values()))
        for case in cases:
            outputs=[]
            for run in runs:
                if run["case_id"]!=case["id"]:
                    continue
                rows=run["queries"][-1]["result"]["rows"] if run["queries"] else None
                if rows is not None and not case["ordered"]:
                    rows=sorted(rows,key=lambda row:exact_dumps(row,sort_keys=True))
                outputs.append(exact_dumps([run["status"],rows],sort_keys=True))
            report["summary"]["per_case"][case["id"]].update(
                distinct_outputs=len(set(outputs)),consistent_output=len(set(outputs))==1)
        report["completed"]=True
        checkpoint()
        return report
