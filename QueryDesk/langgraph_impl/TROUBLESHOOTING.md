# Troubleshooting QueryDesk

Use the symptom tables to identify the failing layer before changing configuration. Commands below run from the application's `langgraph_impl` directory. They assume the Windows launchers and local ports 3000/8000.

## Find the issue

- [Quick diagnosis and logs](#quick-diagnosis-and-logs)
- [Installation and startup](#installation-and-startup)
- [Sign-in and permissions](#sign-in-and-permissions)
- [Agent models and provider policy](#agent-models-and-provider-policy)
- [Warehouse connections and TLS](#warehouse-connections-and-tls)
- [Domains, tables, rows and PII](#domains-tables-rows-and-pii)
- [Query reviews, retries and cancellation](#query-reviews-retries-and-cancellation)
- [Answers, SQL and result tables](#answers-sql-and-result-tables)
- [History and restart recovery](#history-and-restart-recovery)
- [HTTP status reference](#http-status-reference)
- [When a restart or rebuild is needed](#when-a-restart-or-rebuild-is-needed)
- [Report a reproducible problem](#report-a-reproducible-problem)

## Quick diagnosis and logs

Administrators can first open **Operations** to check queue saturation, stage/provider latency and token-reporting gaps. For duplicate/retried submissions and isolated failure reproduction, see [operations and reliability](OPERATIONS_AND_RELIABILITY.md).

1. Note the question's job ID, current stage, exact visible error, source and time. Check whether it is waiting for a table or SQL decision.
2. Check API reachability through the frontend and directly:

```powershell
Invoke-RestMethod 'http://127.0.0.1:3000/api/health' -TimeoutSec 5
Invoke-RestMethod 'http://127.0.0.1:8000/api/health' -TimeoutSec 5
```

A response with `service: querydesk` confirms that the API is reachable. The health response does not test every remote warehouse, provider credential, agent profile or SQL query. Use the source connection test and a reviewed sample question for those checks.

3. For the managed launcher, inspect the appropriate local log:

```powershell
Get-Content .\outputs\ui\api-error.log -Tail 80
Get-Content .\outputs\ui\web-error.log -Tail 80
```

`api.log` and `web.log` contain standard output; `api-error.log` and `web-error.log` contain standard error. Terminal-launched development services write to their terminals instead. Log files may not exist if startup failed before launching a service. Some API errors are intentionally generic, and a log may not contain the underlying provider/SQL payload.

Check logs locally before sharing excerpts. Do not include `.env`, API keys, warehouse passwords, cookies, CSRF tokens, private questions, result rows or encryption keys in an issue report. Do not enable external model tracing to diagnose a problem: the data policy blocks it.

## Installation and startup

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Python or Node/npm command is missing | Prerequisite is not installed or its executable is outside PATH | Install the project prerequisites on the host, open a new terminal, and run `python --version`, `node --version`, `npm --version`. End users only need a browser. |
| Missing `.venv`, module import error or missing connector package | Incomplete installation or a different Python environment | Run `.\setup-ui.ps1` for the complete app; `.\setup.ps1` installs the Python application. Use this app's `.venv\Scripts\python.exe`. |
| Dependency installation fails | Network, package-registry access or incompatible host environment | Inspect the first installation error and verify registry access. Preserve the lock file; do not solve a download failure by deleting dependency constraints. |
| UI is not set up / frontend build missing | No `web/dist/index.html` | Run `.\setup-ui.ps1`, which installs frontend packages and builds the UI. |
| Port 3000 or 8000 is already in use | Existing managed session or another service | For a managed session use `.\stop-ui.ps1`. Identify any other listener before stopping it; do not terminate an unrelated process. |
| A UI session is already recorded | `outputs/ui/processes.json` already exists | Use `.\stop-ui.ps1`, then start again. The script verifies process identity. If it refuses or process inspection is unavailable, have the operator inspect the recorded processes; do not blindly delete the record or kill PIDs. |
| Stop script says the recorded process does not match | PID was reused or the directory/process record changed | Verify the actual command and owning application. The refusal protects unrelated processes; do not bypass it with a blanket kill. |
| Page opens but requests return 502 | Node is serving the UI but cannot reach FastAPI | Check the API log and direct port-8000 health request. Confirm the frontend proxy targets the intended local API. |
| Backend works directly, but the page cannot query | Frontend proxy/origin mismatch or stale page assets | Open the supported local UI origin, refresh the page, and check the frontend log. See [UI startup/development](UI.md). |
| Moving the folder breaks launchers or imports | Virtual environments contain installation-specific paths | Recreate the virtual environment at the new location using the setup instructions. Preserve `.env`, configuration, data and encryption keys. |
| Old controls remain after an upgrade | Served frontend build predates source changes | Run `npm run build` from `web`, then refresh the browser. Restart the managed frontend if its serving configuration changed. |

Check Python dependency consistency without changing the installation:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

## Sign-in and permissions

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Administrator setup is already complete | The application records database already has an administrator | Use sign-in. Do not delete the records database to recreate an administrator; it also contains security/history state. |
| Password is rejected at setup/onboarding | Password length is outside 15–128 characters | Choose a password within the supported range. |
| Invalid account name or password | Credentials are incorrect or the account is unavailable | Confirm the account and ask an administrator to check its enabled state. Avoid repeated automated login attempts. |
| Too many authentication attempts | Authentication rate limit | Wait the displayed interval; the rate-limit error can specify 15 minutes. A separate “sign-in is busy” response means temporary hashing capacity pressure. |
| Repeated sign-in prompts on local HTTP | Expired session, blocked cookies, mixed hostnames or secure-cookie configuration | Use one hostname consistently. Local HTTP needs `AUTH_COOKIE_SECURE=false`; an HTTPS deployment should use secure cookies. Sign in again after session/access changes. |
| Action returns 403 while the page is open | Missing/stale CSRF token, origin restriction, changed access or an admin-only action | Refresh and sign in again if needed; use permitted UI actions. Custom clients must retain the session cookie and send `X-CSRF-Token`. |
| Administrator cannot see a warehouse table | Source allowlist or database login does not permit it | Admin status does not override configured source scope or database grants. Verify both. |
| Another user's query cannot be cancelled/edited | Cancellation and mutation require ownership | Use the owning account. Admin visibility does not grant ownership of these actions. |

See [authentication and inherited permissions](SECURITY.md). Never share another user's session or edit password hashes directly as a troubleshooting shortcut.

## Agent models and provider policy

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Model is unconfigured | Missing shared key/model or a different environment file | Check this app's `.env`, `NLQ_ENV_FILE`, and matching process variables. The app does not search parent directories. |
| Changing the UI model does not change SQL/another agent | Explicit role model takes precedence | Inspect `LLM_SQL_MODEL` or the corresponding role variable in the file and process environment. Remove the override if the agent should follow the UI fallback. |
| A role endpoint requires its own API key and model | Role endpoint is set without both role credentials/model | Set `LLM_<ROLE>_API_KEY` and `LLM_<ROLE>_MODEL`, or remove the role endpoint to inherit the shared connection. |
| Early stages work but a later agent fails | Later agent uses a different provider, model, budget or policy profile | Check all six effective role configurations, not only the shared model. Use the local check in the [agent configuration guide](AGENT_MODEL_CONFIGURATION.md#5-restart-and-verify). |
| No exactly matching approved data-handling profile | Missing approval, wrong endpoint/model, or overlapping approved profiles | Match the normalized base URL and exact model ID to exactly one approved profile. Multiple agents can share one profile. Review the approval before changing its flag. |
| Source classification cannot be sent | Profile excludes the classification, or source requires a local endpoint | Use a permitted destination. Do not lower source classifications to make a request pass. |
| Model context exceeds payload budget | Schema, examples, question or tool context exceeds the profile's byte cap | Narrow relevant metadata/domains/tables or shorten the question. Change the cap only within supported bounds after reviewing the data policy. |
| Possible credential or sensitive personal information detected | Question or context contains locally detected sensitive text | Remove secrets/PII from the question and use safe identifiers. PII egress needs both explicit profile permission and a sender grant; application PII-view permission alone is insufficient. |
| External model tracing must be disabled | External tracing environment variable is enabled | Disable the relevant `LANGSMITH_TRACING`, `LANGCHAIN_TRACING` or `LANGCHAIN_TRACING_V2` setting in the backend process. |
| Provider reports 401/403 or configuration failure | Wrong key, model access, endpoint or provider permission | Check the affected role's server-side configuration and provider account. Do not paste keys into the question or policy JSON. |
| Provider reports HTTP 429 | Provider quota/rate limit | Check quota and reduce concurrent calls. This differs from the app's local queue limit. SDK transport retries are disabled; repeatedly resubmitting can worsen the limit and incur costs. |
| Cannot reach configured provider / timeout | Connectivity, endpoint or request timeout | Verify outbound HTTPS access and the role endpoint. Adjust `LLM_<ROLE>_TIMEOUT_SECONDS` within supported bounds when appropriate. |
| Invalid tool handoff or provider rejects a parameter | Model lacks required tool behavior or API-style/optional-parameter mismatch | Check role API style, strict tools and parallel-parameter settings. Malformed handoffs have at most three calls within the global budget. Native incompatible APIs need an adapter. |
| Response incomplete or filtered | Output budget or provider filtering | Inspect the role token cap and provider restrictions. Increasing a cap cannot resolve a provider policy refusal. |

See [per-agent settings](AGENT_MODEL_CONFIGURATION.md), [multiple-provider samples](MODEL_POLICY_EXAMPLES.md) and [call/retry budgets](QUERY_CONTROLS.md). Policy approval alone does not verify credentials or live tool-call compatibility.

## Warehouse connections and TLS

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Source is not configured | Source profile's connection environment variable is unset | Set the exact variable named by `connection_env`, then restart the backend. Keep the URL/password server-side. |
| Cannot open source / connection test fails | Network, credentials, connector dependency, schema or grants | Check hostname/port reachability, driver installation, URL format and read-only login grants. A UI source connection test does not verify every future SQL query. |
| Configured table missing / source profile rejected | Wrong database/schema, unsupported object or allowlist mismatch | Verify actual regular tables and exact profile identifiers. The app intentionally scopes supported objects; not every view/external/nested object is supported. |
| Certificate or verified TLS error | Wrong hostname, missing/untrusted CA, expired certificate or incompatible protocol | Correct the host/certificate/CA configuration. Use [engine-specific TLS settings](TLS_CONNECTIONS.md). Do not disable verification as the default fix. |
| Development plaintext setting still cannot connect | Cloud service enforces encryption or explicit URL retains TLS | `WAREHOUSE_TLS12_ENABLED=false` permits development plaintext where supported; it does not force it. Snowflake and BigQuery retain verified HTTPS. |
| AWS/GCP authentication fails | Expired or unavailable deployment credentials, wrong identity/project/location or missing IAM/database grants | Verify the runtime identity and configured connection method. BigQuery uses ADC or the configured service-account file; validate dataset, location and billed-byte cap. Consult the [connector guide](WAREHOUSE_CONNECTORS.md). |
| Warehouse query fails although connection test passed | Query permissions, dialect, query timeout, schema change or cost limit | Review effective SQL, bound values, selected source and the warehouse's native error/job information. Re-plan after schema changes. |
| Cross-database SQL Server join is refused | Unregistered physical path, missing mapping/grant, different instance or unsupported naming | Register exact mapped tables with stable logical IDs and grant the login read access. This is mapped same-instance SQL Server support, not arbitrary cross-source federation. |
| Database/schema selector is absent | Ordinary source or older review payload uses the flat selector | The grouped selector is for configured mapped SQL Server sources. It is not a general database discovery/connection picker. |

See [sources and domains](SOURCES_AND_DOMAINS.md), [warehouse connectors](WAREHOUSE_CONNECTORS.md) and [SQL Server cross-database configuration](SQLSERVER_CROSS_DATABASE.md). Remote execution/cancellation capabilities still need validation against the deployment's actual warehouse.

## Domains, tables, rows and PII

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Expected domain/table is missing | Source allowlist, direct/group domain grants, stale review or wrong source | Confirm the selected source and permitted domain union; ask an administrator to review grants. A UI domain choice cannot grant access. |
| Tables outside the suggested domain appear | Selected domain is a relevance hint | This is allowed when tables stay inside the account's permitted scope. Confirm only the needed tables. |
| Results differ between two users | Domain, inherited group, row-level or PII permissions differ | Compare authorized scopes and the review's row-security details. Do not assume the unrestricted reference answer applies to every user. |
| Row-policy configuration is invalid | Invalid JSON/schema, subject IDs, attributes or dependency mapping | Correct the configured policy using [ROW_SECURITY.md](ROW_SECURITY.md). User mappings require server-issued account IDs, not usernames. |
| No rows under row security | User has no matching scope attributes or data does not match the configured rule | Review user/group attributes, types, policy composition and actual scope values. Do not disable row security to obtain a result. |
| PII columns are hidden | Account/group policy denies plaintext PII | Ask the administrator to review PII grants. Encryption-enabled and plaintext-view permission are separate controls. |
| Decryption/authentication failure | Wrong key, changed field identity, malformed envelope or incompatible cipher | Preserve the original key and restore the intended mapping/configuration. Do not regenerate a key over existing ciphertext or perform an unreviewed migration. |
| Local PII processing exceeds limits | Preparation/input/plaintext/envelope/cell/decryption-count/time ceiling | Narrow rows/columns and review Source copy scope. Limits cannot be disabled, and row/byte/cell caps cannot exceed supported ceilings. See [BOUNDED_PII.md](BOUNDED_PII.md). |

The database being larger does not itself grant a larger local PII budget. Non-PII direct reads and bounded encrypted-PII processing have different execution paths; see [DIRECT_READS.md](DIRECT_READS.md) and [COPY_REDUCTION.md](COPY_REDUCTION.md).

## Query reviews, retries and cancellation

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Question appears stuck at Review tables | Workflow awaits user input | Select permitted tables and confirm. No SQL is generated until this step completes. |
| Question appears stuck at Review SQL | Final approval is pending | Inspect SQL, parameters, restrictions and limits; approve or cancel. A restart is not needed to continue a normal review. |
| Proposal changed/expired or execution plan changed | Old tab/version, elapsed deadline, edited SQL or changed schema/access/security settings | Refresh and review the current proposal. If no valid proposal remains, run the question again. Do not replay an old approval. |
| Queue full / unfinished query limit | Local active+queued bound, 50 unfinished-job bound or per-user limit | Finish/cancel waiting work and retry later. API 429 may include `Retry-After`; waiting alone will not resolve a per-user limit occupied by undecided reviews. |
| Requests serialize | Default worker count is one | Modest concurrency can be configured using `QUERY_WORKERS` and `QUERY_MAX_PENDING`. Run one API process; distributed workers need additional coordination not implemented here. |
| Attempt/model-call budget exhausted | Repeated invalid handoffs, SQL repairs or edits | Inspect the failing stage and question/schema quality first. Adjust budgets only within bounds and expected cost. Do not endlessly resubmit. |
| Edited SQL rejected | Read-only, table/column, parameter, semantic or other validation failed | Correct the edit within confirmed scope or start a new question. Edited SQL fails closed instead of being silently regenerated. |
| Cannot directly edit SQL during execution | SQL editor is available at final review | Use Stop and edit question, then edit/review the replacement SQL before approving it. |
| Cancellation requested remains visible | Active model/driver call has not returned | Allow its timeout/driver cancellation to complete. Late results are discarded; cancellation is not proof that remote work or billing stopped. |
| Warehouse still shows a running query after Stop | Native cancellation is unsupported, failed or lost a completion race | Check warehouse-native job/session information; ask an authorized operator to cancel there if required. Current SQL Server/MySQL/Snowflake handling does not promise immediate remote termination. |
| New question cannot be submitted after Stop | Previous active call is still draining | Wait for it to exit. Its capacity slot remains charged until then. Editing a draft alone does not cancel any job. |
| Temporary polling error | Network/server interruption | Polling backs off without resubmitting SQL. Check connectivity; avoid creating duplicate questions while execution state is uncertain. |

See [SQL approval](SQL_APPROVAL.md), [non-blocking reviews](NON_BLOCKING_REVIEWS.md) and [query controls](QUERY_CONTROLS.md).

## Answers, SQL and result tables

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Plausible answer has the wrong total | Join fan-out, incorrect business definition, dates/units or model misunderstanding | Compare SQL with documented metrics and an independent reference query. Aggregate independent one-to-many children before joining. Approval/model review is not mathematical proof. |
| Currency is 100 times too large/small | Minor-unit conversion mismatch | The sample stores monetary values in integer hundredths of INR. Inspect metric definitions and requested units in [DATASET.md](DATASET.md). |
| Empty result table | Filters, joins or authorized row scope match no data | Inspect bound values, date ranges, join keys and restrictions. Empty results are not automatically an execution failure. |
| Returned rows differ from full report | Result limit, clipped cell or aggregate truncation | Read the partial-result notice and row cap. Ask a narrower/aggregated question; do not treat a partial result as complete. |
| Columns extend outside the card | Wide report | Scroll the Query results table horizontally. Returned columns are not limited to the visible screen width. |
| Exact decimal/large integer looks like text | Lossless display avoids binary-float rounding | Preserve the exact value. Do not convert it to a browser number just to change its appearance. |
| SQL is visible but no answer appears | Awaiting approval, execution failure, provider failure or explanation failure | Check the current stage and whether the SQL is labelled prepared or executed. If execution completed, rerunning may repeat warehouse work/cost. |
| Sample totals differ from the guide | Older sample, modified data or restricted account | Compare against the intended sample version and authorized scope. `.\run.ps1 --expand-demo` upgrades original sample data while preserving existing rows; it is a data-changing command, not a repair for real sources. |
| Results seem inconsistent across repeated model runs | Non-deterministic generation or semantic weakness | Use the golden/held-out evaluation flow and business definitions in [ENHANCEMENTS.md](ENHANCEMENTS.md); compare actual results, not SQL strings alone. |

## History and restart recovery

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| Saved question returns 404 or disappears | Retention expired, user deleted history or access changed | Expired/deleted records cannot be restored from the UI. A fresh question requires new reviews. |
| Saved answer has SQL but no row grid | `HISTORY_STORE_RESULT_ROWS=false` omitted raw rows | This is intentional. The UI does not reconstruct or auto-rerun data. A new approved run is needed for fresh rows. |
| Restore saved review is offered | An eligible unexpired waiting/not-started checkpoint survived restart | Restore it explicitly, review current scope and approve again. Restoration itself does not run SQL. |
| Restore rejected after changes | Expired checkpoint, changed permissions, plan, limits, encryption settings or configuration | Start a fresh question. Old approvals are never executable authority after restart. |
| Server restarted during execution | Completion state may be uncertain | Check warehouse-native state before repeating the query. The app does not automatically replay uncertain execution. |
| In-flight SQL edit cannot be restored | Edit had not reached a fresh persisted SQL review | Start the question again; an unvalidated edit cannot reuse the previous approval. |
| Browser history cleared but an old backup still contains data | Backups are managed separately from live retention | Apply the operator's backup retention/access policy. Clearing live app history does not erase external backups. |

See [history hardening](PHASE_A.md) and [restart recovery](RESTART_RECOVERY.md).

## HTTP status reference

These are API response meanings. A query submission can return 202 successfully and still later reach a failed job status due to a provider or database error; inspect the job's stage/message too.

| Status | Typical meaning | Next step |
| --- | --- | --- |
| 401 | Missing/expired session or bad login | Sign in again or check credentials |
| 403 | CSRF/origin/access/PII/policy refusal | Review the precise message and authorized scope |
| 404 | Unknown, expired, deleted or inaccessible resource | Verify source/job ID and ownership; do not infer another user's resource exists |
| 409 | Stale review/version, changed access or conflicting state | Refresh and review the current state |
| 415 | POST did not use `application/json` | Custom clients must send the JSON content type, including for `{}` bodies |
| 422 | Input/configuration choice fails validation | Check fields, selected model/domain/tables and payload shape |
| 429 | Local queue/user/auth limit or provider quota | Identify which limit fired before retrying |
| 502 | Frontend proxy cannot reach the API | Check the backend and proxy target |
| 503 | Source/model/service unavailable or invalid required configuration | Check the associated server configuration and dependency/connectivity health |
| 500 | Unexpected server failure | Inspect local logs and report a sanitized reproduction |

## When a restart or rebuild is needed

| Change/action | Required action |
| --- | --- |
| `.env`, process settings, credentials, worker budgets, TLS/encryption flags | Finish/cancel pending work, then restart backend |
| Backend Python code or source/connector configuration | Restart backend to load the intended version/configuration; create fresh reviews |
| Frontend source | Rebuild from `web` with `npm run build`, then refresh browser |
| Current table/SQL confirmation | Use the review buttons; no restart |
| Valid row-policy file edit | Current implementation reloads it and invalidates old reviews; enabling its environment flag still requires a restart |
| Model policy profile edit | Policy is checked before provider calls; use a fresh reviewed run and the local configuration check. Do not assume startup health certifies approval |

Restart managed services from this application's directory:

```powershell
.\stop-ui.ps1
.\start-ui.ps1 -OpenBrowser
```

A restart does not cancel every remote warehouse job or resolve uncertain execution. Verify native job state first when necessary. Do not use restart as a substitute for fixing malformed configuration, missing grants or unsupported adapters.

## Report a reproducible problem

Include the app revision, host/runtime versions, UI origin, affected stage, job ID/time, connector kind, sanitized model ID/endpoint, exact error/status, and a minimal non-sensitive example with expected versus actual behavior. Note whether the failure occurs before approval, during execution or during explanation, and whether warehouse-native work is still running.

Share only redacted configuration keys/values needed for reproduction. Never attach the active `.env`, session/CSRF material, PII, database files containing sensitive data, encryption keys or unrestricted log dumps. If the question contains private information, reproduce against synthetic sample data instead. Explain what you already checked so maintainers can isolate the failing layer.

## Visualization issues

For missing chart data, renderer availability, interactivity preferences, model-policy refusal and chart limits, see [visualization troubleshooting](VISUALIZATIONS.md#troubleshooting-and-verification). A chart failure does not require rerunning the business query.
