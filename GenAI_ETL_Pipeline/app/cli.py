import argparse
import json
from pathlib import Path
from dotenv import load_dotenv
from . import store
from .engine import execute
from .generation import generate
from .ingest import ingest_bytes
from .models import PipelineSpec, RunRequest
from .remediation import apply, propose


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="FlowForge ETL CLI")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo")
    plan = commands.add_parser("plan")
    plan.add_argument("prompt")
    source = commands.add_parser("ingest")
    source.add_argument("file")
    run = commands.add_parser("run")
    run.add_argument("--spec", required=True)
    run.add_argument("--dataset", action="append", required=True)
    run.add_argument("--key", required=True)
    args = parser.parse_args()
    store.init()
    if args.command == "demo":
        from .models import demo_spec
        import uuid
        dataset = ingest_bytes(Path("samples/products.csv").read_bytes(), "products.csv")
        spec = demo_spec()
        spec.max_reject_ratio = .2
        original = execute(RunRequest(spec=spec, dataset_ids=[dataset["id"]], idempotency_key=uuid.uuid4().hex))
        proposal = propose(original["id"])
        healed = apply(proposal["id"])
        result = {"before": {"id": original["id"], "status": original["status"], "report": original["report"]}, "patch": proposal["patch"], "after": {"id": healed["id"], "status": healed["status"], "report": healed["report"]}}
    elif args.command == "plan":
        result = generate(args.prompt)
    elif args.command == "ingest":
        path = Path(args.file)
        result = ingest_bytes(path.read_bytes(), path.name)
    else:
        import yaml
        spec = PipelineSpec.model_validate(yaml.safe_load(Path(args.spec).read_text()))
        result = execute(RunRequest(spec=spec, dataset_ids=args.dataset, idempotency_key=args.key))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
