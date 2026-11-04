import json
import re
import yaml
from .ai import provider, structured
from .models import PipelineSpec, demo_spec


def generate(prompt):
    if provider() != "local":
        spec, meta = structured("Create a PipelineSpec for the user's ETL request. Use supported scalar columns, cleaning, ordered derived_columns, row_filters and optional gold group_by/measures. Multiple source datasets are mapped independently then appended; joins require an explicitly staged merge. Use aliases for messy source names; require the primary key. Do not fabricate credentials or cloud connectors. Destination is the local warehouse logical target. Defaults must be explicitly justified by the user. Return a complete validated plan.", prompt, PipelineSpec)
    else:
        # The offline compiler is deliberately limited and reports unsupported requirements.
        if not re.search(r"product|catalog|sku", prompt, re.I):
            raise ValueError("Local compiler supports product-catalog requests only. Edit a spec manually or enable live AI for other domains.")
        unsupported = re.findall(r"\b(?:join|aggregate|spark|snowflake|bigquery|redshift|airflow|schedule|daily|hourly)\b", prompt, re.I)
        if unsupported:
            raise ValueError("Local compiler cannot implement these requirements: " + ", ".join(sorted(set(unsupported))) + ". Use the local product recipe or author an integration.")
        spec = demo_spec()
        spec.description = prompt
        # Offline defaults only apply when requested explicitly.
        spec.columns[4].default = "USD" if re.search(r"default\s+(?:currency\s+)?(?:to\s+)?USD", prompt, re.I) else None
        if re.search(r"currency|dollar|symbol", prompt, re.I):
            spec.columns[3].transforms = ["currency_number"]
        gate = re.search(r"(?:reject|quarantine)\D{0,20}(\d+(?:\.\d+)?)\s*%", prompt, re.I)
        if gate:
            spec.max_reject_ratio = float(gate[1]) / 100
        spec = PipelineSpec.model_validate(spec.model_dump())
        meta = {"provider": "local", "mode": "bounded product-catalog recipe", "notes": ["CSV/JSON/API dataset selection happens at run time.", "Supported intent: product mapping, scalar cleaning, unit normalization, currency parsing, reject percentage, default USD.", "Review every column, default and rule before execution; arbitrary business logic needs a custom spec or live AI."]}
    return {"spec": spec.model_dump(), "metadata": meta, "artifacts": artifacts(spec)}


def artifacts(spec: PipelineSpec):
    encoded = json.dumps(spec.model_dump(), indent=2)
    # repr produces a Python literal; neither input text nor SQL is interpolated as code.
    python = '"""Generated, reviewable runner. Run from the project root after ingesting a dataset."""\nimport argparse\nimport json\nfrom app import store\nfrom app.models import PipelineSpec, RunRequest\nfrom app.engine import execute\n\nSPEC = PipelineSpec.model_validate(json.loads(' + repr(encoded) + '))\n\nif __name__ == "__main__":\n    parser = argparse.ArgumentParser()\n    parser.add_argument("--dataset", action="append", required=True)\n    parser.add_argument("--key", required=True)\n    args = parser.parse_args()\n    store.init()\n    result = execute(RunRequest(spec=SPEC, dataset_ids=args.dataset, idempotency_key=args.key))\n    print(json.dumps(result, indent=2))\n'
    from .destinations import artifacts as destination_artifacts,ddl
    result={"pipeline.yaml": yaml.safe_dump(spec.model_dump(), sort_keys=False), "generated_pipeline.py": python, "target.sql": "-- Reference DDL; SQLite runtime stores governed JSON rows. Review before cloud deployment.\nCREATE TABLE " + spec.target + " (\n  " + ",\n  ".join(c.name + " " + {"string": "VARCHAR", "integer": "BIGINT", "number": "DOUBLE"}[c.dtype] + (" NOT NULL" if getattr(c,'required',False) else "") for c in [*spec.columns,*spec.derived_columns]) + ",\n  PRIMARY KEY (" + spec.primary_key + ")\n);\n"}
    result.update(destination_artifacts(spec))
    if spec.destination.cloud!='local':result['target.sql']=ddl(spec)
    result['sttm.json']=json.dumps({'business':spec.business.model_dump() if spec.business else None,'primary_key':spec.primary_key,'destination':spec.destination.model_dump(),'columns':[c.model_dump() for c in spec.columns],'derived_columns':[c.model_dump() for c in spec.derived_columns],'row_filters':[r.model_dump() for r in spec.row_filters],'gold':spec.gold.model_dump() if spec.gold else None,'note':'Source aliases are proposed bindings. The run STTM records exact resolved paths per dataset.'},indent=2)
    return result
