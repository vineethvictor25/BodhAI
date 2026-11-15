import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .storage import Database
from .demo import DEFAULT_DATABASE, create_demo, expand_demo


def main():
    parser = argparse.ArgumentParser(description="Separate LangGraph NLQ-to-SQL workflow")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--db", default=str(DEFAULT_DATABASE))
    parser.add_argument("--tables", nargs="+")
    parser.add_argument("--source",default="sample",help="Operator-configured data source ID")
    parser.add_argument("--init-pii-key",action="store_true",help="Initialize the configured local PII key without replacing an existing key")
    parser.add_argument("--pii-migrate",choices=["encrypt","decrypt"],help="Explicit offline local SQLite PII migration")
    parser.add_argument("--apply",action="store_true",help="Apply the explicitly requested offline PII migration")
    parser.add_argument("--backup",help="New encrypted backup file path for a PII migration")
    parser.add_argument('--history-maintenance',choices=['inventory','adopt-legacy','purge-legacy','purge-expired'],help='Inspect or explicitly maintain durable history')
    parser.add_argument("--model", help="Fallback model for specialists without a role-specific model override")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-model-calls", type=int, default=40)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--graph", action="store_true", help="Print the actual compiled graph as Mermaid, without model calls")
    parser.add_argument("--init-demo", action="store_true", help="Create this application's local sample database")
    parser.add_argument("--schema", action="store_true", help="Print permitted local database metadata")
    parser.add_argument("--expand-demo", action="store_true", help="Add richer related sample data without replacing existing rows")
    parser.add_argument("--workspace", nargs="+", help="Restrict routing to sales, payments and/or inventory")
    parser.add_argument("--evaluate", action="store_true", help="Run golden-question evaluation")
    parser.add_argument("--reference-only", action="store_true", help="Verify golden answers without calling a model")
    parser.add_argument("--repeat", type=int, default=3, help="Independent runs per evaluation question")
    parser.add_argument("--case", action="append", dest="case_ids", help="Evaluate a named case (repeatable)")
    parser.add_argument("--limit", type=int, help="Limit evaluation case count")
    parser.add_argument("--report", default="outputs/evaluation.json", help="Evaluation JSON report path")
    parser.add_argument("--pause-seconds",type=float,default=0,help="Pause between evaluation runs to respect provider limits")
    parser.add_argument("--suite",choices=["development","heldout"],default="development")
    parser.add_argument("--resume",action="store_true",help="Resume the same saved evaluation without repeating completed trials")
    parser.add_argument("--eval-retries",type=int,default=2,help="Maximum retries per trial for temporary provider failures")
    parser.add_argument("--retry-delay-cap",type=float,default=60,help="Skip retries whose provider-requested delay exceeds this cap")
    parser.add_argument("--review-feedback",action="store_true",help="Read pending local feedback; does not promote examples")
    args = parser.parse_args()
    if args.resume and not args.evaluate:
        parser.error("--resume requires --evaluate")
    if args.reference_only and not args.evaluate:
        parser.error("--reference-only requires --evaluate")
    if not (args.graph or args.init_demo or args.expand_demo or args.schema or args.evaluate or args.review_feedback or args.init_pii_key or args.pii_migrate or args.history_maintenance) and args.question is None:
        parser.error("provide a question or use --graph, --schema, --init-demo or --expand-demo")
    try:
        if args.history_maintenance:
            from .config import APP_DIR,environment_values
            from .pii import PIIPolicy
            from .persistence import ReviewStore
            values=environment_values()
            store=ReviewStore(APP_DIR/'outputs'/'querydesk.sqlite3',cipher=PIIPolicy(values).cipher,ttl_seconds=float(values.get('HISTORY_RETENTION_HOURS',24))*3600)
            if args.history_maintenance=='purge-expired':
                if not args.apply:raise ValueError('Use --apply to purge expired history')
                print(json.dumps({'expired_deleted':store.maintenance()}))
            else:print(json.dumps(store.legacy_inventory(apply=args.apply and args.history_maintenance!='inventory',purge=args.history_maintenance=='purge-legacy'),indent=2))
            return 0
        if args.init_pii_key or args.pii_migrate:
            import os
            from dotenv import dotenv_values
            from .config import APP_DIR,environment_values
            from .pii import PIIPolicy,initialize_key
            values=environment_values()
            # Key initialization must work before encryption has been enabled successfully.
            policy=PIIPolicy({**values,"PII_ENCRYPTION_ENABLED":"false"})
            if args.init_pii_key:
                initialize_key(policy.key_file)
                print("Local key created. Protect and back up its file separately; its value is not printed.")
                return 0
            if not args.apply:
                raise ValueError("Stop the app, review PII mappings and use --apply to run the offline migration")
            from .pii_migrate import migrate
            print(json.dumps(migrate(args.db,args.source,policy,decrypt=args.pii_migrate=="decrypt",backup=args.backup),indent=2))
            return 0
        if args.review_feedback:
            from .persistence import ReviewStore
            from .config import APP_DIR,environment_values
            from .pii import PIIPolicy
            from dotenv import dotenv_values
            import os
            policy=PIIPolicy(environment_values())
            print(json.dumps(ReviewStore(APP_DIR/"outputs"/"querydesk.sqlite3",cipher=policy.cipher).reviews(),indent=2))
            return 0
        if args.init_demo:
            create_demo(args.db)
            print("Created the expanded sample database with 25 orders and 19 related tables.")
            return 0
        if args.expand_demo:
            applied = expand_demo(args.db)
            print("Added the expanded sample data." if applied else "Sample data is already expanded and consistent.")
            return 0
        def source_database():
            database=Database(args.db,allowed_tables=args.tables)
            if args.source=="sample":
                return database,None
            if args.tables:
                raise ValueError("Use the source profile allowed_tables for external sources")
            from .sources import SourceRegistry
            return SourceRegistry(database).open(args.source)
        if args.schema:
            store,_ = source_database()
            print(json.dumps(store.schema(sorted(store.allowed)), indent=2))
            return 0
        from .workflow import LangGraphWorkflow
        from .models import LangChainModel
        from .config import ModelConfig
        database,business = source_database()
        def factory():
            return LangGraphWorkflow(database, model_factory=lambda role: LangChainModel(config=ModelConfig.for_role(role,args.model),source=args.source,principal_groups=('role:operator',)),
                max_attempts=args.max_attempts, max_model_calls=args.max_model_calls,workspaces=args.workspace,knowledge=business)
        if args.evaluate:
            if args.source!="sample":
                raise ValueError("Bundled evaluation suites target the SQLite sample fixture; use --source sample")
            from .evaluation import Evaluator
            def progress(run):
                print(f"{run['case_id']} run {run['iteration']}/{args.repeat}: " +
                      ("passed" if run["passed"] else run["status"]),file=sys.stderr,flush=True)
            from .config import ModelConfig
            settings={}
            if not args.reference_only:
                configured=ModelConfig.load(model=args.model)
                settings={key:getattr(configured,key) for key in ("model","base_url","api_style","max_tokens","timeout","strict_tools","send_parallel")}
                settings.update(max_attempts=args.max_attempts,max_model_calls=args.max_model_calls,workspaces=args.workspace)
                settings['agent_models']={role:{key:getattr(ModelConfig.for_role(role,args.model),key) for key in
                    ('model','base_url','api_style','max_tokens','timeout','strict_tools','send_parallel')}
                    for role in ('intent','schema','columns','sql','validation','response')}
            report=Evaluator(database,factory,suite=args.suite,configuration=settings).run(
                args.repeat,args.case_ids,args.limit,args.reference_only,args.pause_seconds,progress,
                report_path=args.report,resume=args.resume,max_retries=args.eval_retries,max_retry_delay=args.retry_delay_cap)
            output=Path(args.report)
            print(json.dumps({"report":str(output.resolve()),"references_verified":True,
                "case_count":report["case_count"],"summary":report["summary"]},indent=2))
            return 0 if args.reference_only or report["summary"]["passed"]==report["summary"]["total_runs"] else 1
        workflow = factory()
        if args.graph:
            print(workflow.build_graph().get_graph().draw_mermaid())
            return 0
        result = workflow.ask(args.question)
        from .value_codec import dumps as exact_dumps
        print(exact_dumps(result, indent=2) if args.json else result["answer"])
        return 1 if result["status"] == "failed" else 0
    except ImportError:
        print("LangGraph dependencies are missing. Run langgraph_impl/setup.ps1 and use its run.ps1 launcher.", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
