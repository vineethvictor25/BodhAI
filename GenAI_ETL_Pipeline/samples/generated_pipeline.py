"""Generated, reviewable runner. Run from the project root after ingesting a dataset."""
import argparse
import json
from app import store
from app.models import PipelineSpec, RunRequest
from app.engine import execute

SPEC = PipelineSpec.model_validate(json.loads('{\n  "name": "product_catalog",\n  "description": "",\n  "target": "products",\n  "columns": [\n    {\n      "name": "product_id",\n      "dtype": "string",\n      "required": true,\n      "aliases": [\n        "sku",\n        "item_id",\n        "product.code"\n      ],\n      "transforms": [\n        "trim",\n        "upper"\n      ],\n      "minimum": null,\n      "maximum": null,\n      "allowed": [],\n      "default": null\n    },\n    {\n      "name": "product_name",\n      "dtype": "string",\n      "required": true,\n      "aliases": [\n        "name",\n        "title",\n        "product.title"\n      ],\n      "transforms": [\n        "trim"\n      ],\n      "minimum": null,\n      "maximum": null,\n      "allowed": [],\n      "default": null\n    },\n    {\n      "name": "length_m",\n      "dtype": "number",\n      "required": false,\n      "aliases": [\n        "length",\n        "dimensions.length",\n        "size"\n      ],\n      "transforms": [\n        "length_m"\n      ],\n      "minimum": 0.001,\n      "maximum": 100.0,\n      "allowed": [],\n      "default": null\n    },\n    {\n      "name": "price",\n      "dtype": "number",\n      "required": true,\n      "aliases": [\n        "cost",\n        "pricing.amount",\n        "amount"\n      ],\n      "transforms": [\n        "currency_number"\n      ],\n      "minimum": 0.0,\n      "maximum": 1000000.0,\n      "allowed": [],\n      "default": null\n    },\n    {\n      "name": "currency",\n      "dtype": "string",\n      "required": false,\n      "aliases": [\n        "ccy",\n        "pricing.currency"\n      ],\n      "transforms": [\n        "trim",\n        "upper"\n      ],\n      "minimum": null,\n      "maximum": null,\n      "allowed": [\n        "USD",\n        "EUR",\n        "INR",\n        "GBP"\n      ],\n      "default": "USD"\n    },\n    {\n      "name": "category",\n      "dtype": "string",\n      "required": false,\n      "aliases": [\n        "type",\n        "product.category"\n      ],\n      "transforms": [\n        "trim",\n        "lower"\n      ],\n      "minimum": null,\n      "maximum": null,\n      "allowed": [],\n      "default": null\n    }\n  ],\n  "primary_key": "product_id",\n  "max_reject_ratio": 0.2,\n  "mapping_threshold": 0.82,\n  "mapping_overrides": {}\n}'))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", action="append", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    store.init()
    result = execute(RunRequest(spec=SPEC, dataset_ids=args.dataset, idempotency_key=args.key))
    print(json.dumps(result, indent=2))
