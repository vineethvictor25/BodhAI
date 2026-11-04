# FlowForge — GenAI ETL & Data Quality

## Release 1.5 — file ETL, Medallion stages, destinations and column privacy

Use **Medallion ETL → Stage four-format order example**, then **Pipeline studio → Preview Silver & Gold → Run reviewed pipeline**. Multiple CSV/TSV/JSON/JSONL/TXT/Parquet sources are executable, with append/joins, source profiles/contracts, derived fields, filters, DQ and atomic load strategies. PII columns support AES-GCM, masking and stable keyed tokens. Keys require external configuration; real PII is rejected if its key is missing.

Cloud/warehouse dropdowns validate destination-specific configuration and export plans/DDL. **Cloud publication is not connected.** Local execution remains SQLite. Document extraction is removed from the primary UI; the old endpoint remains for compatibility.

- [File ETL and Medallion guide](docs/MEDALLION_ETL.md)
- [Cloud and destination configuration](docs/DESTINATIONS.md)
- [Column-level PII controls](docs/PII_HANDLING.md)
- [All 49 engineering requirements: exact coverage](docs/ENGINEERING_COVERAGE.md)
- [Operating runbook](docs/ETL_RUNBOOK.md)

The full local suite passes 171 tests. A headless browser verified the four-format sample and destination configuration. Cloud connectors, private network deployment, full RBAC, CDC/streams and production operations remain explicit integration work. Existing data/backups are not retroactively protected.


## Current documentation (release 1.5)

Start with [System guide](docs/SYSTEM_GUIDE.md) and [Architecture](docs/ARCHITECTURE.md). The architecture SVGs and editable Mermaid sources are in docs/diagrams/. The browser view is docs/diagrams/architecture.html. Current implementation and proposed cloud target are clearly separated.


A runnable, production-style reference implementation with a FastAPI backend, a browser console, a local SQLite warehouse, governed transformation recipes, and an optional OpenAI multimodal adapter. Requires Python 3.11+.

This is a single-machine reference application, not a deployed enterprise platform. It implements real ingestion, transformation, validation, quarantine, publication and recovery. Cloud warehouse writers, distributed Spark execution, production identity, durable job scheduling and arbitrary stored-procedure conversion are extension work; they are not represented as working integrations.

## Start on Windows

Dependencies and the virtual environment were installed during implementation. Start from PowerShell:

```powershell
cd .\GenAI_ETL_Pipeline
.\start.ps1
```

Open **http://127.0.0.1:8765**. To choose a different port, use ` .\start.ps1 -Port 9000 `.

For a clean installation or after copying the repository:

```powershell
cd .\GenAI_ETL_Pipeline
.\setup.ps1
.\start.ps1
```

If your PowerShell policy blocks scripts, run the equivalent command directly; no policy change is needed:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1
```

`requirements.lock.txt` records the exact verified dependency versions. For reproducible installation, use `.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt`. `requirements.txt` expresses compatible ranges for later maintenance.

## Try the complete recovery workflow

1. Click **Run sample pipeline** in Overview. Six product records are staged and processed.
2. Inspect the quality report: **3 valid, 3 quarantined, 0 published**. The reject ratio exceeds the 20% gate.
3. Choose **Propose repair**. Review the change from `number` to `currency_number` for price.
4. Click **Approve patch & replay source**. A new run reads the original immutable source snapshot and retains the same gate.
5. The result is **5 valid, 1 quarantined, 5 published**. The negative-length/negative-price record remains rejected. Inspect warehouse rows and the audit trail.

The demo uses synthetic sample data. It changes only this project's local database. The CLI demo explicitly applies the built-in repair as a test scenario:

```powershell
.\.venv\Scripts\python.exe -m app.cli demo
```

## Capabilities and actual boundaries

| Requested capability | Implemented behavior | Boundary |
|---|---|---|
| Natural-language pipeline generation | Validated JSON plan; downloadable executable Python runner, YAML recipe and SQL DDL | Local mode is a bounded product recipe; optional live AI handles other scalar schemas. Sources are selected at execution. Spark and Airflow are not installed. |
| Dynamic schema mapping | Live natural-language + sample-aware semantic mapping, editable source selections, persisted approvals, per-source bindings; offline aliases/fuzzy preview remains available | Confidence is model-reported. No join discovery or computed mappings. Unknown paths fail validation; approved mappings are bound to source snapshots and target identity. |
| Unstructured extraction / ETAI | TXT/MD and text-layer PDFs; attributes, summary, keyword sentiment and evidence; live semantic extraction and image input | PNG/JPEG vision requires a model and credentials. Scanned PDFs need image export first. Extraction is staged for human review. |
| Cleaning and normalization | `200cm` and `2m` → `2.0`; US currency parsing, trimming, casing, explicitly configured defaults, type/range/enumeration/uniqueness checks | No invented missing facts. Units must be explicit. Context filling is an auditable contract default. Semantic anomalies use contract bounds, not statistical learning. |
| Legacy SQL translation | SQLGlot AST translation from SQL Server/Oracle/MySQL/PostgreSQL/Hive/Spark to Snowflake/BigQuery/Redshift/Spark/DuckDB | Procedural scripts are flagged for manual migration. Syntax translation does not establish semantic equivalence or live warehouse execution. |
| Self-healing / remediation | Error-log diagnosis; evidence-based currency parser patch; generated artifacts for revised recipe; explicit approval, new run, unchanged quality gate, parent audit link | Diagnosis is heuristic; unknown failures are not patched. Applies a transformation configuration hotfix, not an arbitrary source-code edit. AI requests have bounded transient-error retries. |

## Build a custom pipeline

Upload `samples/products.csv` and `samples/nested_products.json` in **Data sources**. In **Pipeline studio**, generate a plan from the default request, inspect its JSON contract and download the artifacts. Select both datasets and preview their mappings. Run only after reviewing the inferred source paths and defaults.

The generated specification supports arbitrary scalar target columns, aliases, required fields, numbers/integers/text, bounds, enumerations, defaults, transformations, a required primary key, a reject threshold and mapping overrides. For example:

```json
"mapping_overrides": {"product_id": "sku"}
```

Overrides use full flattened paths such as `product.code`. One source column cannot map to multiple target columns in the current mapper. If two fields match equally well, add an override rather than lowering the threshold blindly.

Generated Python uses the same validated interpreter as the API, rather than evaluating model output:

```powershell
.\.venv\Scripts\python.exe -m app.cli ingest samples\nested_products.json
# Copy the dataset ID from the result, then use the generated YAML:
.\.venv\Scripts\python.exe -m app.cli run --spec samples\pipeline.yaml --dataset DATASET_ID --key my-batch-001
# Or download generated_pipeline.py into the project root:
.\.venv\Scripts\python.exe generated_pipeline.py --dataset DATASET_ID --key my-batch-002
```

The logical destination is an upsert table in SQLite. A rerun with the same idempotency key and identical plan/source snapshot returns the original run. Reusing a key for different content returns HTTP 409. Different batches upsert by `(target, primary_key)`; last committed batch wins. There is no automatic delete propagation or freshness ordering.

## Optional live AI

Copy `.env.example` to `.env` if needed, then set:

```dotenv
PROVIDER=openai
OPENAI_API_KEY=your-key
OPENAI_MODEL=your-available-model
```

Choose a model available to your account that supports structured output and, for images, vision. Restart the server. The key stays on the backend and is never returned to the browser. Requests use the Responses API, JSON schema output, `store=false`, bounded retries and Pydantic validation. Invalid or incomplete responses fail closed. Selecting live mode does not silently fall back to local mode on an error.

Live mode sends the request text, document content or image to OpenAI. Use only data you are authorized to send. This implementation does not automatically redact PII. The adapter has unit tests using mocked provider responses. A live semantic-mapping verification was attempted with synthetic sample data and received HTTP 429, so successful live inference remains unverified. Check API rate limits and project quota before retrying.

Reference: [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create), [image input guide](https://developers.openai.com/api/docs/guides/images-vision?api-mode=responses).

## API ingestion

Set `API_ALLOWED_HOSTS=api.example.com` before calling **Snapshot an API**. Only HTTPS port 443 is supported; private/reserved addresses and redirects are blocked. Responses must contain a JSON object, an array, or an envelope with a `records`/`data` array. The connector supports one bounded unauthenticated snapshot, not pagination, OAuth or incremental CDC. Use an egress firewall to mitigate DNS rebinding in deployed environments.

## Project map

```text
app/
  main.py          API, request controls, local UI
  models.py        Validated executable plan and contracts
  ingest.py        CSV/JSON/API ingestion and snapshots
  mapping.py       Confidence-based schema reconciliation
  cleaning.py      Allowlisted transforms and scalar validation
  engine.py        Quality gates and atomic publication
  store.py         Persistence, lineage and audit events
  ai.py            Optional structured/multimodal Responses adapter
  generation.py    NL compiler and code/YAML/DDL artifacts
  extraction.py    Document/image extraction and staging
  translation.py   Strict SQL dialect translation
  remediation.py   Diagnosis, versioned proposal and replay
  cli.py           Command-line operations
static/            Browser frontend (no build step or external assets)
samples/           CSV, nested JSON, text document, legacy SQL, YAML
tests/             Isolated engine, API, provider and failure tests
docs/              Architecture, API guide, operations and validation
data/              Runtime database, ignored by version control
```

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check static\app.js
```

Tests use isolated temporary databases and need no cloud credentials. See `docs/VALIDATION.md` for the verified outcome and outstanding integration checks. The installed Starlette version emits a TestClient/httpx deprecation warning; test assertions still pass.

## Container option

Set a strong `APP_API_TOKEN` in `.env`, then run `docker compose up --build`. Open http://127.0.0.1:8765 and choose **Configure access token**. The token is held in browser memory only. The container listens internally on 8000; the host port is 8765. Docker execution was not verified in this environment.

Read `docs/ARCHITECTURE.md` and `docs/OPERATIONS.md` before adapting this reference for production.

## New in v1.1: natural-language semantic mapping

Pipeline Studio now includes **Auto-generate schema mappings with natural language**. Select sources, describe their business meaning, generate AI mappings, review paths/confidence/evidence, approve, then execute. Approved per-source bindings are persisted and used by the engine, not replaced by fuzzy inference. See `docs/NATURAL_LANGUAGE_MAPPING.md` for the complete workflow and API. Restart the server and refresh your browser after upgrading.

## v1.2 — focused retrieval, evaluation and deployment assessment

The optional Knowledge & checks console adds reviewed policy/runbook search, cited excerpts and repeatable regression checks. Pipeline Studio can include those references in mapping requests, off by default. Retrieval uses existing SQLite FTS5; there is no vector, agent or training runtime. See docs/KNOWLEDGE_AND_EVALUATION.md and docs/PRODUCTION_READINESS.md. The application remains a local reference, not production-ready. Cloud migration is feasible after identity, storage, job execution, operational and release controls are implemented.

## v1.3 — conversational change review and email outbox

**Change review** imports an existing run as version 1, supports natural-language draft/refinement, shows a versioned diff, evaluates a no-publish preview and requires explicit human approval before activation. Execution is a separate authorization. Rollback restores configuration as a new history version and does not reverse warehouse data. See docs/CONVERSATIONAL_CHANGES.md.

Applied version/mapping/remediation changes queue metadata-only email events transactionally. Delivery is disabled until SMTP sender/recipient settings are configured in .env; see docs/EMAIL_NOTIFICATIONS.md. The workflow uses bounded typed operations and deterministic replay checks rather than autonomous agents. It adds no runtime package. Existing production-readiness limitations still apply.

## v1.4 — multiple models and controlled fallback

PROVIDER now accepts local, openai, anthropic, gemini, groq, deepseek, openai_compatible or router. Use PROVIDER=router with config/llm_routes.yaml to select a primary and ordered backup models, including multiple models from one provider. Keys stay in environment variables. Cross-provider/endpoint fallback needs explicit authorization. Availability failures may retry/fail over; refusals, incomplete responses and invalid output stop. See docs/MULTI_MODEL_ROUTING.md for setup, policy, protocol limits and examples. Existing .env files are not automatically migrated or rewritten.
