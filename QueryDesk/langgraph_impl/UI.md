# QueryDesk UI

The [current architecture diagram](ARCHITECTURE.md) documents the deployed request flow. Follow the [role-specific knowledge guide](DOMAIN_KNOWLEDGE.md#quick-start-by-role) to assign owners, create drafts and publish reviewed definitions.

Open **Domain knowledge** to maintain business definitions through a draft/review/publish lifecycle. Administrators assign owners and reviewers separately from data permissions. See [Domain knowledge](DOMAIN_KNOWLEDGE.md).

For larger analytical queries, select **Analytics lab · local combined mirror** after generating/registering the [analytics lab](ANALYTICS_LAB.md) and restarting the API. The guide provides trend, KPI, cross-domain and predictive-analysis questions plus native SQL Server setup instructions.

If setup, sign-in, a query or a connection fails, see the [troubleshooting guide](TROUBLESHOOTING.md).

See [Models, capacity, editing and cancellation](QUERY_CONTROLS.md) for per-agent `.env` settings, limits, retries and Stop/Edit actions.

Server operators can [assign different models to different agents](AGENT_MODEL_CONFIGURATION.md). Explicit agent model settings take precedence over the browser's model preference; that preference controls agents without an override. Keys and provider approvals are configured on the server.

The local UI uses **React/TypeScript with Node.js** for the frontend and **FastAPI/Uvicorn** for the API. It connects to the existing standalone LangGraph workflow. End users only open a browser: there are no downloads, exports or CSV controls.

## What users see

Completed answers offer **Visualize this result** with Automatic/Interactive/Static presentation. The agent chooses Plotly, Matplotlib or Seaborn and explains why. Suggested attributes/metrics fill a new question for normal approval. See [visualization behavior and limits](VISUALIZATIONS.md).

For mapped cross-database sources, table confirmation now groups permitted objects by **database → schema → table**. AI-suggested tables start selected. Users can toggle an individual table or the permitted tables within a database/schema; partially selected groups show a mixed checkbox. Physical table names are displayed alongside stable logical IDs, so duplicate names remain distinguishable. These choices narrow the confirmed table set; they do not grant access or change the configured connection. Ordinary sources and older review payloads retain the flat selector. Restored reviews rebuild permitted location metadata before confirmation.

Executed query rows now appear in an accessible **Query results** table with column headers and horizontal scrolling. It preserves exact decimal/large-integer display and shows NULL values explicitly. Empty results, limited/clipped output and raw rows omitted from saved history receive distinct notices. The table uses authorized API results directly; it does not depend on the explanation model formatting a Markdown table. Refresh the page after installing the updated frontend build.

Try the [eight-table segment/region report](DATASET.md#example-questions) using Retail sample and an account permitted to access all referenced tables. The unrestricted reference report has 10 rows covering 22 non-cancelled orders. The app displays all returned report columns; wide reports scroll horizontally. Saved history that omits raw rows shows a notice instead of reconstructing or automatically rerunning the query. Re-running requires the normal table/SQL approvals.

- A chat-style question feed. Each question is an independent data query, not an open-ended chatbot conversation.
- The answer and a separate, brief data-based explanation of why that answer follows from the returned data.
- A table review step after schema selection and before SQL generation. Confirm or edit permitted source tables, or cancel the query.
- Prepared SQL before validation/execution, and an explicit label when SQL has actually executed.
- Real stage progress and clear clarification, refusal, provider-limit and failure states.
- A read-only table/field explorer, metric guide, workspace choice and optional model preference.
- Feedback on the answer, SQL or selected tables, stored for review with the associated question and output.
- Source selection and connection testing, plus business domains created manually, loaded from developer JSON or proposed by AI for review. Domains are relevance hints, not strict table rules. See `SOURCES_AND_DOMAINS.md`.

The explanation is a concise justification based on data, aggregates, filters and units, not private model chain-of-thought. Raw result grids and file downloads are not shown.

## Setup and start

From the workspace root, the developer/server operator runs:

```powershell
.\langgraph_impl\setup-ui.ps1
.\langgraph_impl\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

On a fresh installation, `setup-ui.ps1` creates `.env`. Copy the generated token into `langgraph_impl\.env` as `AUTH_SETUP_TOKEN=<printed-token>`, then start the app:

```powershell
.\langgraph_impl\start-ui.ps1 -OpenBrowser
```

The first administrator must enter that token in the browser. The generated token must be 32–256 characters; see [SECURITY.md](SECURITY.md) for details and cleanup. If an administrator already exists, you do not need a setup token. Open **http://127.0.0.1:3000** after startup. The browser downloads normal web-page assets, but users do not install software or download result files. Setup installs Python/Node build dependencies on the development machine; it is not an end-user action.

Stop the managed processes with:

```powershell
.\langgraph_impl\stop-ui.ps1
```

The start script runs hidden local services and records their IDs and logs under `outputs/ui`. The stop script checks process identities before stopping them. Ports 3000 and 8000 must be free. Windows background-process behavior may depend on local policy.

## Development

In two terminals:

```powershell
# FastAPI backend:
.\langgraph_impl\.venv\Scripts\python.exe -m uvicorn langgraph_impl.api:app --host 127.0.0.1 --port 8000

# Node/Vite frontend:
cd langgraph_impl/web
npm run dev
```

Vite serves on port 5173 and proxies `/api` to FastAPI. `npm run build` creates the frontend build; `npm start` serves it through the native Node.js server on port 3000. The production frontend server proxies API calls to the same local backend.

## API

Administrators have an **Operations** screen with queue/capacity gauges, stage/provider/execution latency and reported versus unknown token usage. Submission retries reuse a per-attempt key within the mounted browser app. See [operations and reliability](OPERATIONS_AND_RELIABILITY.md) for persistence, scope and testing limits.

The source picker supports all registered [warehouse connectors](WAREHOUSE_CONNECTORS.md), including AWS Redshift and Google BigQuery. BigQuery SQL approval shows its billed-byte cap and query location. Configure source/domain/PII/row/provider mappings on the server; connection secrets are not sent to the browser.

SQL approval also displays the source transport policy: encrypted connections required, or unencrypted development connections permitted. `WAREHOUSE_TLS12_ENABLED` is configured on the server and changes the approval fingerprint. See [TLS_CONNECTIONS.md](TLS_CONNECTIONS.md); displayed policy is not a measurement of a live negotiated TLS version.

Browser questions pause twice: first to confirm tables, then to [approve the final effective SQL](SQL_APPROVAL.md) and bound values before data reads. Use **Approve SQL and run** to proceed or **Cancel without reading data** to stop. Approval expires and is invalidated by plan/access changes. This is separate from the application's internal single-use execution approval.

When [row security](ROW_SECURITY.md) is enabled, SQL review also shows each table's mode, inherited restriction SQL and bound scope values. Answers/reasons disclose row-limited results. Access and security displays enabled status plus account/group IDs for policy-file mapping; membership stays editable there, while predicate/attribute values are managed in the server file. Valid JSON policy changes reload and invalidate old reviews without restarting the server. Enabling its environment flag still requires one backend restart.

The review panel now labels **Direct protected read** or **Bounded local PII processing**. Supported SQLite non-PII queries run directly against protected source projections rather than copying rows first; classified-PII queries retain bounded local handling. The approved fingerprint includes this path, capabilities and available source-schema identity. See [DIRECT_READS.md](DIRECT_READS.md) and the [future connector contract](CONNECTOR_CONTRACT.md).

For local PII handling, **Source copy scope** lists each source SELECT, its bound values and the required transferred columns. This scope is fingerprinted and rechecked. [Selective copying](COPY_REDUCTION.md) preserves full local query evaluation and retains the transfer ceiling; safe non-PII lookup conditions can reduce a large table to the requested records before decryption.

**Local PII limits** displays effective row, input/plaintext byte, envelope/cell, decryption-count and preparation-time limits. These limits participate in approval; exceeding one stops the attempt without a partial result or automatic SQL repair. See [BOUNDED_PII.md](BOUNDED_PII.md) for configuration and timing limits.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/admin/operations` | Admin-only process-local queue, latency, failure and token aggregates |
| `GET /api/health` | Service/database readiness |
| `GET /api/config` | Safe UI preferences, model IDs, domains and starter questions |
| `GET /api/schema` | Permitted table metadata |
| `GET /api/metrics` | Permitted metric definitions |
| `POST /api/queries` | Submit an independent English question and receive a job ID |
| `GET /api/queries/{id}` | Poll stage events, planned tables/SQL and final answer/reason |
| `POST /api/queries/{id}/confirm` | Confirm/edit the current table proposal or cancel before SQL generation |
| `POST /api/queries/{id}/approve-sql` | Approve/cancel the current version and plan fingerprint; accepted approval queues a fresh segment |
| `POST /api/queries/{id}/restore-review` | Restore an eligible saved review with current permissions and a new proposal version; does not run SQL |
| `POST /api/queries/{id}/feedback` | Save helpful/needs-work feedback and an optional correction for review |

`POST` accepts `question`, `workspace` (the UI domain hint), `source` and optional `model`. It does not accept SQL, database paths, credentials or conversation history. Model choices are limited to the configured model plus supported Groq choices when using Groq. Server-side `UI_DATABASE` and optional comma-separated `UI_ALLOWED_TABLES` configure the default source; additional profiles have their own table permissions.

By default the API runs one workflow segment at a time, with up to three queued/running segments and 50 unfinished jobs including waiting reviews. Worker, queue and per-user limits are configurable; see [query controls](QUERY_CONTROLS.md). [Table and SQL reviews release the worker](NON_BLOCKING_REVIEWS.md), allowing other users to query. No SQL is generated until table confirmation; no business-data reads occur until final SQL approval. Each repair attempt proposes tables again and requires fresh SQL approval. Versions, deadlines, permissions and model/attempt budgets remain enforced across segments. A domain guides relevance; additional tables stay within source permissions and the user's assigned domain union. Run one API process. Normal reviews need no restart. After restart, use [Restore review](RESTART_RECOVERY.md) for an eligible checkpoint; expired/changed reviews and running/uncertain executions require a fresh question run.

### Multi-instance deployment readiness

The current job manager, review continuations, queue limits and single-use approval state are process-local. Review/history and account records are stored in SQLite, but sharing that file does **not** coordinate two API processes; a second process can see a saved review without sharing the first process's in-memory job lock or continuation. Do not run multiple API workers/replicas against the same application database.

Before enabling multiple instances, implement and test:

- A shared durable job queue with atomic claims/leases, cancellation, bounded retries and idempotent continuation; never replay an execution whose outcome is uncertain.
- Shared coordination for review restoration and single-use approval consumption, with owner/access-policy rechecks and race tests across separate processes.
- Distributed rate limiting and capacity limits, including provider quotas and per-user controls.
- Monitoring for queue depth, waiting/expired reviews, execution/recovery outcomes, provider failures/latency, database errors and rate-limit responses, without recording sensitive question/result payloads.
- Operational runbooks for graceful shutdown, crash recovery, backup/restore and key rotation.

Current tests exercise two authenticated users through one API process and safe review/history recovery after restart. They do not establish multi-process safety or availability.

Query snapshots, review proposals/decisions, feedback and private review checkpoints are saved in `outputs/querydesk.sqlite3`, separate from the read-only sample database. Checkpoints contain plain planning artifacts and counters, never live objects or reusable execution tickets. They use the existing encryption/exact-value policy, retain original deadlines and require renewed approval after restoration. New history expires 24 hours after submission by default; raw result rows are omitted from durable snapshots by default. History is owner-scoped and permission-checked after restart; administrators can inspect live saved queries but only the owner can restore one. Browser storage contains per-account job IDs only. **Hide list** clears browser references; **Delete saved history** confirms and deletes the current account's live saved queries, checkpoints and feedback from the application database. Backups are managed separately. Legacy records without checkpoints cannot be restored. See [PHASE_A.md](PHASE_A.md) for retention settings, migration, exact-number serialization and model approval; `SECURITY.md` for encryption; `ENHANCEMENTS.md` for evaluation commands.

## Boundaries

Both services bind to loopback. Provider credentials remain in the Python application's `.env` and are never included in frontend configuration. Raw model traces are excluded from UI responses. SQLite read-only permissions, validation and execution approvals remain enforced.

The UI requires authentication, offers first-run administrator setup and restricted self-onboarding, and enforces direct/group domain and PII permissions. Domain administration is admin-only. Scripts still bind to loopback; shared-network/HTTPS deployment needs reviewed host/origin/proxy settings and secure cookies. See `SECURITY.md` for operation and current limits.

## Verification commands

```powershell
.\langgraph_impl\.venv\Scripts\python.exe -m unittest discover -s langgraph_impl/tests -v
cd langgraph_impl/web
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

API tests are explicitly skipped if FastAPI has not been installed. Frontend verification requires the local Node dependencies. The Playwright test starts a temporary local API and sample SQLite database, serves the production frontend, and runs a deterministic workflow fixture; it does not read provider credentials or call an external model. Neither offline suite establishes live-model accuracy or bypasses provider quotas.

Latest local verification: all 238 Python/API checks and 46 frontend checks passed; the production build, Python dependency scans and Playwright browser E2E passed; npm audit reported no vulnerabilities. The browser test creates a first administrator using a test-only setup token, submits a question, confirms tables, approves SQL, and verifies the result through the real local API and sample SQLite database using deterministic workflow behavior. It uses no external provider credentials. API tests also cover concurrent users in one process and persisted history/review recovery after restart. Multiple API processes are not tested or supported; see the readiness section above. External warehouse/server verification remains pending.
