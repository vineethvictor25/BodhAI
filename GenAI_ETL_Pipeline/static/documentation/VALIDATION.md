# Verification record

Validated locally on 2 November 2026 with Python 3.11.9. Dependency versions are recorded in `requirements.lock.txt`.

## Executed checks

| Check | Result |
|---|---|
| `python -m pytest -q` | **55 passed**, one upstream Starlette/httpx deprecation warning |
| `node --check static/app.js` | Passed |
| Python module compilation | Passed |
| Standalone CLI sample and remediation | Passed; evidence in `outputs/demo-evidence.json` |
| Generated Python runner | Executed in a test against a persisted dataset; normalized 200cm to 2m and published through the engine |
| Generated YAML | Parsed and compared with the validated specification |
| FastAPI HTTP routes | Verified using in-process TestClient, including upload → failed run → proposal → approval → replay → warehouse |
| Uvicorn startup on 8765 | Startup completed; port bind check succeeded |
| Browser/TCP reachability | v1.1 preview on port 8766 verified through browser DOM and screenshot. Earlier restricted-process attempts on 8000/8765 timed out. |
| Live OpenAI call | Attempted with configured model and synthetic sample data. Service returned HTTP 429 after bounded retries; no successful live inference claimed. Provider behavior and mapping execution verified with mocks. |
| Docker and live cloud warehouse/API sources | **Not run**; Docker configuration supplied, cloud destination adapters are extension work |

## Demo reconciliation

| Stage | Input | Valid | Quarantined | Published |
|---|---:|---:|---:|---:|
| Strict numeric parser, 20% reject gate | 6 | 3 | 3 | 0 |
| Reviewed currency parser, same gate | 6 | 5 | 1 | 5 |

The repair recovers `$1,299.00` and `$49.00`. It leaves the negative length and price rejected. The default USD fill is explicitly configured and recorded in row provenance. The prior failed run remains failed and the replay has its own recipe, run ID, parent link and audit events.

Tests also cover equivalent measurement units, ambiguous units/currency rejection, non-finite values, exact integer validation, duplicate keys, missing required values, nested mapping, mapping ambiguity and overrides, target schema drift, concurrent idempotency, changed-request key conflicts, malformed source files, token checks, cross-origin mutation rejection, upload limits, staged document extraction, API allowlist policy, SQL syntax translation, procedural SQL/manual review, arbitrary-transform rejection and invalid/incomplete model output.

## User-side browser check

The v1.1 browser preview is running at http://127.0.0.1:8766/. The default startup script remains on 8765. Restart your existing server to load the upgrade, then refresh the page. From your own PowerShell:

```powershell
cd C:\GenAI_ETL_Pipeline
.\start.ps1
```

Visit `http://127.0.0.1:8765/health`, then the console at `http://127.0.0.1:8765/`. If another process occupies that port, choose a free one using `.\start.ps1 -Port 9000`. No unknown processes need to be terminated.

## v1.1 semantic mapping checks

The expanded 55-test suite additionally verifies semantic proposal generation with bounded profiles, exact approved mapping use by the engine, hallucinated/duplicate source path rejection, missing target rejection, low-confidence manual selection, approval immutability, source/schema binding, approval HTTP routes, unchanged idempotency semantics, and non-retryable quota-error handling. No live API call is made by the automated unit tests. Screenshot: `outputs/schema-mapping-console.png`.

## v1.2 focused update — 3 November 2026

The final suite has 67 passing tests. Added checks cover overlapping chunk offsets, reviewed-reference retrieval, source citations, draft exclusion, credential/injection signals, fabricated evidence rejection, absent-topic abstention, retrieval without a model key, API access checks, the fixed evaluation cases, optional mapping context and rejection of invented knowledge citations. The evaluation evidence is in outputs/knowledge-evaluation.json. The suite makes no live model call. No production, load, cloud deployment or real-world model-accuracy certification is implied.

LangGraph, vector search, response caching, training export and cloud scaling templates were deliberately removed/deferred after assessing fit. This release uses no new runtime package. See PRODUCTION_READINESS.md. Restart a local server to load version 1.2.0; an existing 1.1 preview was not stopped because its process identity could not be reverified.

## v1.3 — conversational changes and transactional notifications

The final suite has 91 passing tests. Workflow checks verify immutable versions, refinement, source-bound mappings, idempotent change/run requests, preview with no publication effects, preserved quality policy, invalid/hallucinated edit rejection, stale-review conflicts, explicit approval and acknowledgements for identity/new-rejection risk, separate execution, and configuration-only rollback. SMTP tests verify atomic outbox insertion, metadata filtering, disabled mode, bounded retry state, configuration errors, stable delivery identity and TLS before authentication. No real email or live model call was used. Isolated end-to-end evidence is saved in outputs/change-workflow-evidence.json.

The browser preview on port 8767 was verified through registering the existing synthetic sample, creating an offline change and showing the no-publish replay. The demo draft remains pending human review; it was not activated through the browser. Existing servers on other ports need a restart to load version 1.3.0.

## v1.4 — multiple models and fallback

120 tests pass with mocked provider responses. New checks cover retry/fallback for availability failures, quota/model-unavailable handling, total call budget, safety/validation stop behavior, native/compatible request formats, image-route filtering, manual primary switching, configuration redaction, and explicit cross-provider/cross-endpoint permission. No new live provider call was made, no account credits were used, and private .env contents were not changed. Existing live-account and production-readiness limits remain.

## v1.5 — executable Medallion ETL and column protection

156 tests pass (one upstream TestClient/httpx deprecation warning). New fixtures cover real CSV/TSV/JSON/JSONL/TXT/Parquet ingest/export, multi-source preview/replay, safe joins, source profiling/drift/reconciliation, Gold rebuilds, critical/warning checks, append/full refresh/partition overwrite/SCD2 and atomic rollback. Privacy tests cover randomized authenticated column encryption, separate stable HMAC tokens, protected-only replay, missing-key rejection, diagnostic redaction, outbound AI blocking, join context rebinding and step-up decryption audit. Destination tests cover all catalog combinations, configuration validation and refusal to silently publish cloud-target requests locally.

A real headless Chrome run verified the four-file sample: 8 input, 5 accepted, 1 rejected, 2 filtered/superseded, Gold revenue 710. Azure SQL selection exposed its own fields and an export-only load plan. No model call, email send, cloud deployment or live Key Vault/warehouse connection was part of this verification. CI configuration is supplied; hosted CI and Docker builds were not run.
