# Operations, load/failure testing and submission idempotency

The next-stage [durable coordination feasibility assessment](DURABLE_COORDINATION_FEASIBILITY.md) describes shared queues, worker leases, execution uncertainty and migration prerequisites. It is a proposed design, not enabled functionality.

These features support the current **single API process** deployment. They add operational evidence and safe request replay; they do not turn the process-local executor into a distributed durable queue.

## Admin Operations screen

Sign in as administrator and choose **Operations** in the sidebar. It refreshes every five seconds and offers manual refresh. Members cannot access it; the API also enforces administrator access.

The authenticated endpoint is `GET /api/admin/operations`. It returns aggregates only:

| Measurement | Meaning |
| --- | --- |
| Queue gauges | Queued/running segments, table/SQL reviews, unfinished jobs, pending cancellation and configured worker/admission limits |
| Job counters | New submissions, completions, failures, Stop requests/cancellations, admission rejections and idempotency replay/conflict/expiry events |
| Queue wait | Time between enqueueing a workflow segment and its worker start; each resumed segment is measured separately |
| Stage latency | Intent, workspace, schema, columns, SQL, validation, execution and response timings, grouped by outcome |
| Provider calls | Client request duration and bounded outcomes such as rate limit, connection failure, timeout, server or configuration error |
| Warehouse execution | Client-observed execution-adapter duration, including access enforcement, local preparation, fetching and conversion performed by that adapter |
| Tokens | Provider-reported input/output/total usage by agent, reported-call count and unknown-usage count |

Latency aggregates contain count, mean, maximum and cumulative fixed histogram buckets. P95 is the enclosing bucket upper bound, not an exact percentile. A null upper bound means above the 1,200-second final finite bucket. Stage timing excludes human review waits. Warehouse timing is not database-native CPU/execution time and includes overhead such as local encrypted-PII processing. Metadata-only planning is outside the execution-adapter metric.

Token totals include complete normalized usage reported by LangChain/provider responses. Missing/partial reports and failed requests without usage increment **unknown usage calls**; they do not imply zero usage or zero cost. Usage received from a model before cancellation is counted even when the answer is discarded. Provider calls that succeed at transport but return malformed tools are separate from transport failures; handoff/stage validation still detects them. These metrics are not a price estimate or billing reconciliation.

Metrics collected here concern query workflow workers. Standalone CLI runs and unrelated API model features are not automatically included. Gauges cover jobs currently known to this manager, not every un-restored record in storage. Fixed stage/outcome labels bound memory growth; questions, SQL, cell values, users, job IDs, endpoints, keys and raw error bodies are not metric labels or payloads. Provider/model-specific diagnosis uses the agent configuration and existing protected job information.

Aggregates are **in memory and reset on API restart**; the response includes process start time and uptime. There is no historical time-series store, automatic alerting, public Prometheus endpoint or external tracing integration in this increment. Configure authenticated collection, retention and alerting separately if operational history is required. Do not expose this admin endpoint anonymously.

## Submission idempotency

`POST /api/queries` accepts an optional `Idempotency-Key` header. The browser supplies one automatically. Existing clients without a key retain their earlier behavior, so custom clients must adopt the header to receive replay protection.

Use a new random UUID for each intentional submission. For a retry after timeout, lost response or a retryable admission failure, reuse both the same key and the same request body. Keys accept 16–128 ASCII letters/digits, hyphens or underscores. The server normalizes the validated input, including defaults, before matching it.

| Situation | Behavior |
| --- | --- |
| New key and admitted request | Atomically persist the key binding, lifecycle and queued snapshot, then enqueue one job |
| Same owner/key, same request and access stamp | Return the original job ID/current state, with `submission_replayed: true`; do not generate or execute again |
| Same owner/key with changed input or access scope | HTTP 409; no second job |
| Different owner, same key | Independent owner-scoped submission; no other user's job is disclosed |
| Deleted/expired original job | HTTP 410 while the binding/tombstone is retained; never recreate that job from a retry |
| Queue full before admission | HTTP 429; no binding is claimed, so retrying the same key is safe |

Successful original and replay responses use HTTP 202, even if the original job has already completed. Replay does not mean the query ran again. A repeated question with a **new key** is an intentional new run. Idempotency does not deduplicate by question text.

Bindings survive restart in the application records database. The raw key is not stored: the key is hashed, and a key-derived HMAC fingerprints normalized input. The initial queued snapshot substitutes a placeholder for the not-yet-reviewed question, preventing the new registration step from retaining raw input before egress checks; later reviewed/completed snapshots follow existing history and encryption rules. A crash before those snapshots may therefore leave only the placeholder question in history. Binding expiry is tied to the original history expiry, never extended by retries. Minimal bindings remain after deletion/expiry to block resurrection, and maintenance removes them after the existing additional seven-day tombstone window. After cleanup the server can no longer recognize an old key: clients must not reuse keys for unrelated future work.

The browser retains an uncertain attempt's key in memory and reuses it if the unchanged question is resubmitted in the same mounted app. Receiving a successful response clears that attempt; **New question** or changed input starts a new one. The browser does not persist unacknowledged attempt keys across reloads. Custom clients needing retry after their own restart must persist the key themselves. After a browser reload with uncertain submission state, check job history/warehouse state rather than blindly running the question again.

An original job whose server stopped before a safe checkpoint may be returned as failed/uncertain; idempotency does not resume execution. Eligible saved reviews still require explicit restoration and fresh approval. Review mutations use their existing version/fingerprint guards; idempotency keys apply to initial submission only. This is not an exactly-once guarantee for arbitrary warehouse work, nor support for multiple independent API processes.

Minimal custom-client request shape:

```http
POST /api/queries
Content-Type: application/json
Idempotency-Key: 22cdcbf4-2772-4e8a-8ccb-e3ac19b70c88
X-CSRF-Token: <current-session-csrf-token>
Cookie: qd_session=<current-session-cookie>

{"question":"How many orders?","source":"sample","workspace":"auto"}
```

Session, ownership, CSRF and current access checks remain mandatory. Never place credentials inside the question. Preserve the key across a retry and treat a 409/410 as an explicit state to resolve, not permission to silently mint another key and run again.

## Isolated load and failure exercises

The reusable harness runs FastAPI through an in-process ASGI test client with temporary sample/records databases, synthetic authenticated users and scripted provider responses. It drives actual table/SQL review requests and repeats each admitted submission with its key. It never calls a real model provider or a real external warehouse, and does not change your sample database, active credentials or policy approvals. Standard application configuration is still loaded for startup, so malformed local configuration can prevent the exercise from starting.

From the application's directory:

```powershell
.\.venv\Scripts\python.exe -m langgraph_impl.load_test --users 4 --requests 20 --workers 2 --scenario normal --report outputs/load_test/normal.json
.\.venv\Scripts\python.exe -m langgraph_impl.load_test --users 4 --requests 12 --workers 2 --scenario provider_outage --report outputs/load_test/provider-outage.json
.\.venv\Scripts\python.exe -m langgraph_impl.load_test --users 4 --requests 12 --workers 2 --scenario database_timeout --report outputs/load_test/database-timeout.json
.\.venv\Scripts\python.exe -m langgraph_impl.load_test --users 4 --requests 12 --workers 2 --scenario cancellation --report outputs/load_test/cancellation.json
```

| Scenario | Evidence checked |
| --- | --- |
| Normal | Concurrent user jobs complete, duplicate submissions return the original IDs, and each admitted job executes one read |
| Provider outage | Simulated provider connection failures are observed; jobs fail, no business query executes and the queue drains |
| Database timeout | A deliberately expensive SQLite query reaches the real SQLite execution deadline and fails without unbounded repair |
| Cancellation | Stop requests prevent reads for these unapproved jobs; late work is suppressed and the queue drains |

Reports contain aggregate outcomes, accepted-job P95 latency, load-window elapsed time/accepted-job throughput, admission rejection fraction, read attempts, invariants and operational metrics. Exit status is nonzero when an invariant fails. Total elapsed time additionally includes setup/teardown. A passing report may include explicit admission rejections; inspect the rejection fraction when assessing capacity. Users are bounded to 1–20, requests to 1–500 and workers to 1–16. Scripts do not establish a universal latency SLO or a production throughput number.

The regression suite additionally checks concurrent atomic claims, different request/access conflicts, restart/deletion/expiry replay, owner isolation, admin metrics access, known versus unknown usage, bounded metric labels and cancellation racing with late completion. Existing checks cover native SQLite interruption, stale/duplicate approval and active-call cancellation.

Run the full backend/API checks:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
```

Run the frontend checks/build from `web`:

```powershell
npm test
npm run build
```

Before shared production use, separately test real HTTP/proxy/network behavior, realistic schemas/data sizes, actual provider quotas/tool support, warehouse roles/TLS/timeouts/native cancellation and representative question accuracy. The isolated harness cannot certify those. Multi-process failover, durable work dispatch, distributed rate limiting, billing-grade token reconciliation and central monitoring remain future work.
