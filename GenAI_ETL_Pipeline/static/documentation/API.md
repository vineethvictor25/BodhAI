# API quick reference

> Release 1.5 current behavior: see [MEDALLION_ETL.md](MEDALLION_ETL.md), [DESTINATIONS.md](DESTINATIONS.md), [PII_HANDLING.md](PII_HANDLING.md) and [ENGINEERING_COVERAGE.md](ENGINEERING_COVERAGE.md). These supersede older UI/format descriptions below. Cloud selections export plans only; column protection requires externally configured keys.


Base URL: `http://127.0.0.1:8765`. Send `Authorization: Bearer <token>` if APP_API_TOKEN is configured. All mutation bodies are JSON except multipart file uploads. The full OpenAPI specification is served at authenticated `GET /api/schema`.

| Method and path | Purpose |
|---|---|
| GET /health | Database liveness, no data payload |
| GET /api/config | Provider mode, default specification and byte/row limits; no secrets |
| GET /api/datasets | Latest 100 source summaries |
| POST /api/datasets/upload | Multipart `file`: CSV, JSON, JSONL or NDJSON |
| POST /api/datasets/api | `{ "url": "https://approved.example/data" }` |
| POST /api/extract | Multipart `file`: TXT, MD, text PDF, PNG or JPEG |
| POST /api/generate | `{ "prompt": "..." }` → spec, provider metadata and artifacts |
| POST /api/artifacts | PipelineSpec → Python, YAML and reference DDL strings |
| POST /api/mapping | `{ "dataset_id": "...", "spec": {...} }` → mappings and source sample |
| POST /api/runs | `{ "spec": {...}, "dataset_ids": ["..."], "idempotency_key": "..." }` |
| GET /api/runs | Latest 100 run summaries |
| GET /api/runs/{id} | Stored recipe, quality report and audit events |
| GET /api/runs/{id}/quarantine | Full rejection data; offset and limit (1–1000) |
| GET /api/warehouse | target (default products), offset and limit (1–1000) |
| GET /api/warehouse/targets | Published targets and row counts |
| GET /api/warehouse/validate | `target` → read-only full-target schema, type and primary-key consistency results |
| POST /api/translate | `{ "sql": "...", "source": "tsql", "target": "snowflake" }` |
| POST /api/diagnose | `{ "log": "redacted error log" }` → heuristic advice, no mutation |
| POST /api/runs/{id}/remediation | Reviewable numeric-parser recipe patch |
| POST /api/remediations/{id}/apply | `{ "approved": true }` → a new run against original sources |
| POST /api/demo | Synthetic sample ingest and quality-failed run |

Execution is synchronous on a worker thread. A client timeout does not imply failure: find the run by list endpoint or replay the same idempotency key. Concurrent repeated requests may return the allocated run while its status is still running; GET the run until it finalizes. Expected data-quality or contract failures are successful HTTP responses with a non-success **run status**, not HTTP 500. HTTP 409 means an idempotency key conflicts with a different request. Request-model errors return 422 and operational input errors return 400.

## Minimal Python client

```python
import httpx
from pathlib import Path

with httpx.Client(base_url="http://127.0.0.1:8765", timeout=120) as client:
    source = client.post("/api/datasets/upload", files={
        "file": ("products.csv", Path("samples/products.csv").read_bytes())
    })
    source.raise_for_status()
    plan = client.post("/api/generate", json={
        "prompt": "Load product catalog, normalize lengths, parse currency symbols, default currency to USD, quarantine at most 20% invalid rows"
    })
    plan.raise_for_status()
    # Review plan.json()['spec'] before executing.
    run = client.post("/api/runs", json={
        "spec": plan.json()["spec"],
        "dataset_ids": [source.json()["id"]],
        "idempotency_key": "client-example-batch-001"
    })
    run.raise_for_status()
    print(run.json()["status"], run.json()["report"])
```

The JSON schema and enums in `app/models.py` are the authoritative recipe contract. Downloaded YAML is a recipe consumed by the CLI, not an Airflow deployment manifest. SQL artifacts are reference DDL; the SQLite runtime stores records as governed JSON rows.

## Schema discovery and semantic mapping

| Endpoint | Request and result |
|---|---|
| `POST /api/mappings/discover` | Requires `instructions` and two to five distinct `dataset_ids`. Accepts `combination_mode` (auto, append, join), `destination`, and `use_knowledge`. Returns `discovery.field_equivalences`, candidate keys, key evidence, combination and per-source mappings. A blocked draft has null `id` and `spec`. |
| `POST /api/mappings/generate` | Takes `instructions`, `spec`, `dataset_ids`, and optional `combination_mode` and `use_knowledge`. Returns a proposal for the existing contract. |
| `POST /api/datasets/merge/preview` | Evaluates explicit mode, source IDs and key paths. Returns row impact, schema and join cardinality without staging a source. |
| `POST /api/datasets/merge` | Stages a reviewed combined source with parent lineage and row impact. |
| `GET /api/mappings/{id}` | Retrieves a persisted proposal and its approval status. |
| `POST /api/mappings/{id}/approve` | Records complete per-dataset decisions with `approved: true`. The run request accepts the ID as `approved_mapping_id`. |

Temporary rate limiting during a mapping correction with no usable response returns HTTP 429. A rate-limited correction with usable partial discovery data can return a blocked draft. See [Natural-language mapping](NATURAL_LANGUAGE_MAPPING.md) and [Multi-source mapping](MULTI_SOURCE_MAPPING.md).

## v1.2 reference lookup

See KNOWLEDGE_AND_EVALUATION.md for the authenticated /api/knowledge and /api/evaluations endpoints and the optional use_knowledge mapping flag. There is no /api/agents or training endpoint.

## v1.3 versioned changes and notification delivery

See CONVERSATIONAL_CHANGES.md for /api/pipelines and /api/changes, and EMAIL_NOTIFICATIONS.md for /api/notifications. Activation and execution are separate authenticated operations. Configuration rollback does not reverse prior data writes.

## v1.4 model gateway

GET /api/config now returns sanitized llm_routing readiness and policy. Model-error responses include llm_error with a category and sanitized attempt history. Successful model-assisted responses identify the actual provider/model/route and fallback use. See MULTI_MODEL_ROUTING.md.
