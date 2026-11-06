# FlowForge — System Guide and Architecture

> Release 1.5 current behavior: see [MEDALLION_ETL.md](MEDALLION_ETL.md), [DESTINATIONS.md](DESTINATIONS.md), [PII_HANDLING.md](PII_HANDLING.md) and [ENGINEERING_COVERAGE.md](ENGINEERING_COVERAGE.md). These supersede older UI/format descriptions below. Cloud selections export plans only; column protection requires externally configured keys.


**Release:** 1.5.0
**Updated:** 6 November 2026
**Project:** `GenAI_ETL_Pipeline`
**Status:** Working local reference implementation; not production-ready.

## Contents

1. Purpose and implementation boundaries
2. Architecture and component responsibilities
3. Data lifecycle and quality gates
4. Natural-language generation and schema mapping
5. Conversational modification and human review
6. Email notification delivery
7. Knowledge retrieval, evaluations and guardrails
8. Running and configuring the application
9. API reference
10. Storage, recovery and operating limits
11. Cloud-native evolution and technology decisions
12. Verification and supporting documentation

## 1. Purpose and implementation boundaries

FlowForge combines an ETL/data-quality engine with a local review console and optional LLM assistance. It can ingest structured records, infer mappings, normalize values, enforce quality rules, stage document extractions, translate declarative SQL, and manage reviewed changes to existing recipes.

The defining boundary is **proposal versus execution**. Model output is a structured proposal, validated against server-side schemas. The deterministic interpreter executes a fixed transformation vocabulary. It never evaluates arbitrary generated Python, shell commands or SQL. The downloadable Python runner calls the same validated interpreter.

| Capability | Implemented | Important limit |
|---|---|---|
| Pipeline generation | JSON specification, executable Python runner, YAML recipe and reference SQL DDL | Offline compiler is a bounded product-catalog recipe; Spark/Airflow execution is not installed. |
| Schema mapping | Offline aliases/fuzzy matches; optional live sample-aware semantic mapping with review | Joint AI discovery covers field equivalences, key candidates and append or two-source join proposals. Local checks validate proposed keys; business meaning needs review. |
| Unstructured extraction | Labelled text/text PDFs locally; optional semantic extraction and image vision | Scanned PDFs require page-image export; extraction is staged for review. |
| Cleaning and DQ | Unit conversion, number/currency parsing, casing, explicit defaults, bounds, allowed values, required values and key uniqueness | Defaults are explicit contract values; missing facts are not invented. |
| Legacy translation | SQLGlot declarative dialect translation | Stored procedures/control flow require manual migration and real-target semantic tests. |
| Remediation | Reviewed numeric-to-currency parser repair for supported failures | Unknown failures do not trigger arbitrary code edits. |
| Conversational changes | Typed patch proposals, refinement, diff, no-publish preview, human approval, versioned execution and rollback | Target/schema migrations, source replacement and arbitrary code changes are outside this workflow. |
| Applied-change email | Durable transactional outbox, metadata-only SMTP messages and retries | SMTP and recipients must be configured; delivery is at-least-once. |

No cloud warehouse writer, vector service, Redis cache, autonomous agent runtime, fine-tuning service or cloud autoscaler is deployed.

## 2. Architecture and component responsibilities

The current system is **one FastAPI process**, a static browser frontend, a local SQLite database and an optional in-process notification worker. Its modules are not independent microservices.

See `diagrams/architecture-current.svg` for the implemented architecture, `diagrams/architecture-cloud-target.svg` for the proposed target, and `diagrams/architecture.html` for a browser-readable view. Editable Mermaid sources are supplied alongside the diagrams.

| Component | Implementation | Responsibility |
|---|---|---|
| Review console | `static/index.html`, `app.js`, `knowledge.js`, `changes.js` | Sources, recipes, mapping approval, quality reports, SQL migration, knowledge lookup, change review and notification status. |
| API and request controls | `app/main.py`, `control_api.py`, `changes_api.py` | Validated HTTP boundaries, existing access checks and thread offloading for longer operations. |
| Contracts | `app/models.py` | Typed specifications, transformation enums and run requests. |
| Ingestion | `app/ingest.py` | Bounded CSV/JSON/API snapshots, hashes and persisted parsed records. |
| Mapping | `app/mapping.py`, `semantic_mapping.py`, `schema_discovery.py`, `source_combination.py` | Offline inference; optional model-assisted proposals; immutable approved decisions. |
| Generation/model adapter | `app/generation.py`, `ai.py`, `llm_router.py` | Recipe compilation, structured Responses requests, bounded retries and fail-closed validation. |
| Extraction/translation | `app/extraction.py`, `translation.py` | Document staging and declarative SQL conversion. |
| Deterministic execution | `app/cleaning.py`, `engine.py` | Pure transform/DQ replay plus transactional publication. |
| Versioned changes | `app/pipeline_changes.py` | Typed edits, immutable versions, preview, review, activation, managed execution and configuration rollback. |
| Knowledge/evaluations | `app/knowledge.py`, `evaluations.py`, `guardrails.py` | Reviewed full-text references, exact evidence checks, regression cases and conservative input signals. |
| Persistence | `app/store.py` | SQLite transactions, source/run lineage, version history and audit events. |
| Email | `app/notifications.py` | Transactional outbox, SMTP TLS, bounded delivery attempts and status. |

The optional external services are the configured LLM route and an approved SMTP server. Credentials remain on the backend. The console never receives model or SMTP secrets.

## 3. Data lifecycle and quality gates

1. **Ingest:** upload a file or snapshot an allowlisted public HTTPS JSON endpoint.
2. **Persist bronze:** save parsed records, source metadata and a SHA-256 of the original input bytes. Original file/document bytes are not retained by this implementation.
3. **Map:** infer per-source target bindings or use explicitly approved semantic mappings.
4. **Transform:** apply only allowlisted operations such as `trim`, `upper`, `length_m`, `number` and `currency_number`.
5. **Validate:** check required values, finite numbers, exact integers, bounds, allowed sets and within-batch primary-key uniqueness.
6. **Retain silver/quarantine:** keep accepted typed records and rejected original parsed records with source-row provenance and issues.
7. **Publish:** if at least one row is valid and the rejection ratio is within the configured threshold, atomically upsert valid rows into the local logical warehouse target. A target-schema incompatibility also blocks publication.

A quality-failed batch publishes **none of its new records**. It does not erase earlier successful records. When a batch passes, it may publish accepted rows while retaining some rejected rows within its allowed threshold.

Publication uses `(target, primary_key)` upserts. Distinct batches are last-commit-wins; automatic deletion propagation and source-freshness ordering are not implemented. Duplicate keys within one batch keep the first valid occurrence and quarantine later duplicates.

Run idempotency keys identify a particular request. Repeating the same key with the same fingerprint returns the existing run; reusing it for different content returns HTTP 409. Managed version references participate in the fingerprint.

## 4. Natural-language generation and schema mapping

Pipeline Studio separates **target-contract generation** from **source-to-target mapping**.

- Local generation compiles a supported product-catalog request into a bounded recipe. It reports unsupported requirements rather than claiming generic natural-language understanding.
- Live generation uses the configured model and validates the resulting `PipelineSpec` before any execution.
- Offline mapping uses flattened paths, aliases and fuzzy similarity. Ambiguous required fields need review/overrides.
- Live semantic mapping sends natural-language instructions, the target contract and bounded profiles: observed types/nulls and up to three unique sample values per field, truncated to 80 characters each.
- Optional reviewed-reference retrieval can add up to four matching knowledge chunks. It is off by default.

Every target must appear exactly once in a semantic proposal. Invented/reused paths and invalid reference citations are rejected. Low-confidence suggestions are left unselected. Approval explicitly decides every target field; required fields cannot remain unmapped. Saved approval is bound to exact source snapshot IDs/hashes and target identity.

An approved run consumes the exact saved bindings. **Infer schema, keys & mappings** suggests equivalent fields, a target contract and key candidates across two to five sources. Local checks test candidate keys across complete snapshots. Review the **Data model** for field lineage, then stage a reviewed join or approve mapped append. See [Multi-source mapping](MULTI_SOURCE_MAPPING.md).

## 5. Conversational modification and human review

Open **Change review** to manage an existing run as a versioned pipeline.

### Workflow

1. **Register:** import a completed run's recipe, source hashes and actual per-source bindings as version 1. No data is rerun or published.
2. **Describe:** request a change in live AI mode or explicitly select the supported offline grammar.
3. **Draft/refine:** create a new immutable proposal, optionally linked to an earlier draft in the conversation.
4. **Review diff:** inspect the exact operations and unified difference against the active version.
5. **Preview:** replay baseline and candidate on the same pinned sources using the shared pure transform/DQ function.
6. **Human approval:** acknowledge the exact diff/preview and any newly rejected rows or record-identity change. The server blocks failed/stale previews and missing acknowledgements.
7. **Activate:** create a new immutable version and move the active pointer. Activation itself publishes no rows.
8. **Execute separately:** explicitly authorize running the exact active version. Normal quality gates and atomic publication still apply.
9. **Rollback configuration:** approve restoration of an earlier definition as another new history version.

### Supported edits and policy limits

Typed edits cover transformation sequences, literal per-source mappings, defaults, stricter bounds, required fields, allowed values, and reject/confidence thresholds. Conversational changes cannot weaken the reject threshold, reduce mapping confidence, make a required field optional, relax existing bounds or broaden enumerations.

Target names, column names/types, source snapshots and primary-key identity are not schema-migration operations in this workflow. Changing the primary-key field's mapping or transformation is flagged as a record-identity risk and needs acknowledgement; the primary-key column name itself remains fixed.

Previews compare totals, valid/rejected counts, recovered/new rejections and record differences. Displayed record differences are bounded, but acceptance/gate counts evaluate the full pinned batch. Preview writes only its review report and audit event—not runs, silver, quarantine, target contracts or warehouse rows.

### Rollback boundary

**Rollback restores configuration, not warehouse data.** It does not undo earlier upserts, delete newly introduced keys, restore prior values or cancel in-flight work. Data rollback requires before-images/versioned destination tables and a separately approved reconciliation operation.

Managed versions pin their source snapshots. Replacing a source batch is outside the conversational edit vocabulary. This is a replay/review facility, not a scheduler or CDC service.

### Human identity boundary

The current access model is local/shared-token access. Review actions are durable, but do not establish an individual SSO approver or separate author/reviewer roles. Human review is part of this workflow; it is not enterprise separation of duties. General ad hoc APIs still exist for other tasks and need role-based authorization before multi-user production use.

## 6. Email notification delivery

Applied version activation/rollback, approved mappings and applied remediation queue a notification **in the same transaction** as their qualifying audit event. Optional publication notices can also be enabled. Drafts and previews do not trigger applied-change emails.

The in-process worker leases an outbox entry, sends through verified STARTTLS/SSL and records SMTP acceptance. Transient failures retry with backoff, up to five attempts; configuration/authentication/recipient failures are visible as blocked. Failed/blocked delivery can be retried manually after settings are fixed.

Emails contain allowlisted event/version/change/proposal/run identifiers and UTC time. They omit source rows, full recipes, prompts and credentials. SMTP recipients come from backend configuration, never from model output.

Delivery is disabled by default and remains unverified until configured. Pending records are retained while disabled; review the backlog before enabling, because it may be delivered to the recipients configured at send time.

SMTP is **at-least-once**: acceptance followed by a crash can produce duplicates, and SMTP acceptance does not prove inbox delivery. Stable Message-IDs aid deduplication but cannot guarantee it. A dedicated worker, provider delivery/bounce tracking and recipient-level retries are future production work.

## 7. Knowledge retrieval, evaluations and guardrails

The knowledge feature deliberately uses SQLite FTS5/BM25 for the current small corpus. References must be explicitly marked reviewed before retrieval. Documents are split into overlapping chunks with immutable IDs, hashes and character offsets. Local lookup returns cited excerpts without a model call. Optional LLM answers must cite an actual retrieved chunk and an exact quote.

Valid citations establish reference identity and quote validity—not that a generated statement is semantically entailed. Known injection/credential patterns are conservative checks with false positives and evasion limits; they do not provide complete prompt-injection resistance, moderation or PII redaction.

The fixed regression cases measure retrieval recall/MRR, exact evidence references, rejection of known injection strings, an unrelated-question abstention case and fabricated-citation rejection. They are small synthetic wiring/regression checks, not production accuracy or safety certification.

There is no demonstrated live mapping-accuracy improvement from retrieval yet. Keep it optional until representative held-out schemas/rules show better mapping accuracy, fewer reviewer corrections, and acceptable latency/cost.

The fixed change path does not currently justify autonomous agents, LangGraph, general tool routing, Redis, a vector service or fine-tuning. See the promotion criteria in section 11.

## 8. Running and configuring the application

### Local startup

```powershell
cd C:\GenAI_ETL_Pipeline
.\setup.ps1       # clean install / dependency verification
.\start.ps1       # default: http://127.0.0.1:8765
```

Use ` .\start.ps1 -Port 8765 ` or another free port. The BodhAI instance was verified on 8765. Restart an existing server when its backend code changes. Run **one Uvicorn process**.

If PowerShell blocks script execution, use the equivalent command without changing system policy:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1
```

### Configuration reference

| Variable | Purpose / default |
|---|---|
| `PROVIDER` | `local`, `openai`, `anthropic`, `gemini`, `groq`, `deepseek`, `openai_compatible` or `router`; only explicitly selected availability fallback is allowed. |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Backend-only live configuration; choose a model available to the account with the needed structured/vision capabilities. |
| `APP_API_TOKEN` | Shared bearer token. Without one, APIs accept loopback clients only. Required for container/non-loopback clients. |
| `DATA_DIR` | Runtime database directory, default `data`. |
| `API_ALLOWED_HOSTS` | Exact public HTTPS API host allowlist; default empty. |
| `MAX_UPLOAD_BYTES` | Default 31,457,280 bytes (30 MiB). |
| `EMAIL_ENABLED` | Default `false`; enables the SMTP outbox worker. |
| `EMAIL_NOTIFY_PUBLICATION` | Default `false`; optional run-publication messages. |
| `SMTP_HOST`, `SMTP_PORT` | Approved SMTP host and port; STARTTLS default port 587. |
| `SMTP_TLS_MODE` | `starttls` or `ssl`; plaintext mode is unsupported. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Configure both, or neither for an approved relay. |
| `EMAIL_FROM`, `EMAIL_TO` | Sender and up to 20 comma-separated plain recipient addresses. |

Keep secrets in `.env` or the chosen deployment's secret manager, not chat, code or API bodies. `.env` is excluded from the archive and source-control patterns. Restart after changing settings.

Live requests send the selected instructions, bounded source profiles and optional reference content to the configured provider. Only send data you are authorized to transmit. No claim of zero provider retention is made by the adapter's `store=false` setting.

### Input limits and connector scope

- CSV/TSV/Parquet sources: up to 30 MiB and 25 million rows per file, ingested and processed in 25,000-row chunks. JSON/JSONL/NDJSON/TXT and API snapshots remain capped at 25,000 rows. Pipeline runs exceeding 25,000 total rows report progress and publish atomically after critical checks pass.
- Semantic mapping proposals support up to five sources and 100 flattened fields per source. A join stage uses two sources. Disk-backed merge has no fixed total-row ceiling. The separate transformation preview accepts up to 25,000 input rows.
- Extraction: TXT/MD, unencrypted text-layer PDFs up to 30 pages, or PNG/JPEG in live vision mode. Text limit 60,000 characters.
- Knowledge: up to 100,000 characters per reference, default 1,000-character chunks/150 overlap, at most 5,000 chunks locally and ten results per retrieval.
- API ingestion: one bounded, unauthenticated public HTTPS JSON snapshot on port 443; no redirects, private addresses, pagination, OAuth or CDC.

### Docker

Dockerfile/Compose packaging is supplied. The container runs as a non-root user, listens internally on 8000 and maps host port 8765 to loopback. Compose requires `APP_API_TOKEN` and persists the local database in a volume. Docker execution and cloud deployment were not verified in this environment.

## 9. API reference

Data APIs require the configured bearer token, or permitted loopback access when no token is set. JSON model errors return 422, expected operational validation errors return 400, and conflicting version/idempotency state returns 409. Quality/contract failure is a **run status**, not necessarily an HTTP error. `/health` and the static shell are public; `/api/schema` exposes the authenticated OpenAPI document.

| Area | Routes |
|---|---|
| Configuration | `GET /health`, `GET /api/config`, `GET /api/schema` |
| Sources | `GET /api/datasets`, `POST /api/datasets/upload`, `POST /api/datasets/api` |
| Assistance | `POST /api/generate`, `/api/artifacts`, `/api/mapping`, `/api/extract`, `/api/translate`, `/api/diagnose` |
| Schema discovery and semantic mappings | `POST /api/mappings/discover`, `POST /api/mappings/generate`, `GET /api/mappings/{id}`, `POST /api/mappings/{id}/approve` |
| Execution | `GET/POST /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/quarantine`, `GET /api/warehouse` |
| Remediation | `POST /api/runs/{id}/remediation`, `POST /api/remediations/{id}/apply` |
| References | `GET/POST /api/knowledge`, `POST /api/knowledge/search`, `/api/knowledge/answer`, `/api/knowledge/demo` |
| Evaluations | `GET/POST /api/evaluations` |
| Managed pipelines | `GET/POST /api/pipelines`, `GET /api/pipelines/{id}`, `GET /api/pipelines/{id}/versions/{revision}` |
| Conversational changes | `POST /api/pipelines/{id}/changes`, `GET /api/changes/{id}`, `POST /api/changes/{id}/preview`, `/api/changes/{id}/activate` |
| Version operations | `POST /api/pipelines/{id}/run`, `/api/pipelines/{id}/rollback` |
| Email status | `GET /api/notifications/status`, `GET /api/notifications`, `POST /api/notifications/{id}/retry` |

Field-level payload examples and acknowledgement requirements are in the supporting guides. List/report endpoints have bounded previews; full quarantine supports pagination. Execution/model calls are synchronous work offloaded to threads in relevant async routes—not a durable distributed job service.

## 10. Storage, recovery and operating limits

| Storage group | Main tables | Role |
|---|---|---|
| Sources and runs | `datasets`, `runs`, `lineage` | Parsed snapshots, hashes, recipes, statuses and source/run relationships. |
| Quality and warehouse | `silver`, `quarantine`, `warehouse`, `target_contracts` | Typed accepted records, rejected source rows, logical targets and schema compatibility. |
| Mapping governance | `mapping_proposals` | Model suggestions, review decisions and approval scope. |
| Version governance | `managed_pipelines`, `pipeline_versions`, `pipeline_changes` | Active pointer, immutable history, conversational proposals and previews. |
| Knowledge/evaluation | `kb_documents`, `kb_chunks`, `kb_fts`, `kb_meta`, `evaluation_runs` | Reviewed references, search index, corpus revision and regression outcomes. |
| Audit/delivery | `events`, `notification_outbox` | Application audit events and durable SMTP delivery state. |

SQLite uses WAL, foreign keys and a busy timeout. Data publication, version activation/rollback and their qualifying outbox events use local transactions. Application history is not tamper-proof against a database administrator.

On startup, previously running ETL runs are marked interrupted; replay after review with a new key. Pending or expired notification delivery resumes when enabled. This startup model requires one process and must be redesigned before multiple API replicas share control data.

Use SQLite's online backup API for a live database; do not copy only the main file during WAL writes. Test restore into a separate data directory before switching users. Establish retention and encryption policies for source rows, rejected records, profiles, prompts and audit data. Original document bytes need separate archival if required.

The present system does not implement named-user authorization, tenant isolation, immutable external auditing, a general retention/purge service, durable ETL queues, lease-based ETL recovery, cancellation, real warehouse connectors, complete PII handling or production load/security tests.

## 11. Cloud-native evolution and technology decisions

The cloud diagram is a **proposed design**, not an available deployment switch.

Recommended sequence:

1. Define cloud/warehouse, workload, data classification, single-/multi-tenant scope, availability and recovery objectives.
2. Add OIDC/SSO, role-based access, named reviewers, managed secrets, TLS and controlled ingress/egress.
3. Move control records to managed PostgreSQL with migrations; archive original sources/documents in encrypted, versioned object storage.
4. Separate stateless APIs from queue-backed ETL/model workers. Add leases, retry/cancellation/drain behavior and idempotent destination publication.
5. Add real source/destination integration tests, observability, backup/restore and warehouse reconciliation.
6. Establish CI test/scan gates, immutable image releases, staging, monitored rollout and tested rollback. Scale only after measuring concurrency and queue behavior.

| Technology | Decision | Evidence needed before promotion |
|---|---|---|
| Vector retrieval / pgvector | Deferred | Representative synonym/multilingual/corpus queries demonstrate keyword recall gaps; semantic retrieval improves held-out relevance. |
| Hybrid search / reranking | Deferred | Relevance judgments show a gain worth the added latency/cost. |
| Redis | Deferred | Repeated expensive requests create measured cost/latency; cache scope/invalidation includes source, schema, policy and model/prompt versions. |
| Agent orchestration | Deferred | Actual branching, multi-step tool use or durable external approvals justify more than the current typed review workflow. |
| Fine-tuning | Deferred | Curated labels, a held-out baseline and recurring errors remain after prompt/retrieval improvements. |
| Kubernetes/autoscaling | Not required by the current workload | A chosen platform and measured workload justify the operational complexity after durable storage/workers exist. |

A first cloud pilot can use managed containers; Kubernetes is not a prerequisite. Neither an outbox worker nor thread offloading should be mistaken for a distributed ETL queue. Redis must not become the authoritative store for approvals or version history.

## 12. Verification and supporting documentation

The latest completed verification recorded **197 passing tests**, with one upstream TestClient/httpx deprecation warning. Tests use isolated data and mocked model/SMTP responses. Syntax/dependency and archive checks passed. This documentation refresh does not change runtime behavior.

The isolated workflow evidence shows:

| Step | Outcome |
|---|---|
| Original sample | 6 inputs, 3 accepted, 3 quarantined, 0 published |
| Candidate preview | 5 accepted, 1 rejected, 0 published |
| Human activation | Version 2 created; no publication |
| Separate execution | 5 rows published |
| Configuration rollback | Version 3 restores v1 definition; 5 warehouse rows remain |
| Notifications | Activation and rollback events queued; no real email sent |

Live Groq mapping and discovery were verified with synthetic data, including SKU and Design Number/Style equivalence. Other model/provider combinations and live edit inference require separate verification. SMTP delivery remains unverified without sender/recipient/provider configuration. Docker, cloud deployment and production load/security behavior are not certified by these tests.

Supporting documents:

- `ARCHITECTURE.md` — architecture index and editable diagrams.
- `NATURAL_LANGUAGE_MAPPING.md` — semantic mapping proposals and approval scope.
- `CONVERSATIONAL_CHANGES.md` — versioned changes, preview, review and rollback.
- `EMAIL_NOTIFICATIONS.md` — SMTP configuration, retries, privacy and delivery limits.
- `KNOWLEDGE_AND_EVALUATION.md` — reviewed reference retrieval and evaluation limits.
- `API.md`, `OPERATIONS.md`, `PRODUCTION_READINESS.md`, `VALIDATION.md` — detailed API/operations and release evidence.

Evidence files: `outputs/change-workflow-evidence.json`, `outputs/knowledge-evaluation.json` and `outputs/demo-evidence.json`.

## Release 1.4 routing update

The model gateway supports named primary/backup models, native OpenAI Responses and Claude Messages adapters, Gemini, Groq and DeepSeek via their documented compatible APIs and other configured compatible servers. Router mode reads config/llm_routes.yaml, bounded attempt/time budgets and explicit cross-provider permission. Successful metadata identifies the actual model and fallback history; /api/config exposes sanitized route readiness. There is no automatic offline fallback or safety/validation-error provider shopping. See MULTI_MODEL_ROUTING.md. The automated suite uses mocked APIs; additional provider/model combinations still require live compatibility checks.
