# Hardening plan for governed, shared NLQ use

Status: implemented increments cover [Phase A](PHASE_A.md), [SQL approval](SQL_APPROVAL.md), [non-blocking reviews](NON_BLOCKING_REVIEWS.md), [safe recovery](RESTART_RECOVERY.md), [row security](ROW_SECURITY.md) and [direct protected SQLite reads](DIRECT_READS.md). The [connector contract](CONNECTOR_CONTRACT.md) defines shared governance and adapter requirements. Classified-PII processing remains bounded. Live warehouse/runtime-role verification, independent security review, representative load validation, report/audit/backup retention, distributed capacity and later gates remain unfinished. The detailed design below includes goals beyond these increments.

## Assessment and priorities

Later completed increments also include [selective copying](COPY_REDUCTION.md), [bounded encrypted PII](BOUNDED_PII.md), [additional warehouse adapters](WAREHOUSE_CONNECTORS.md), [configurable TLS](TLS_CONNECTIONS.md) and a complete shared dependency list in `requirements.txt`. The detailed plan below remains a record of proposed work; current feature guides distinguish implemented behavior from pending verification.

The following assessment records the original baseline before the implemented increments listed above. Use the current guides for completed behavior and remaining limitations.

All eight concerns identify real limitations. For issue 7, the UI can already display prepared SQL during validation because the API emits a SQL event; polling does not guarantee users see it before execution, and no final SQL approval is required. There is a further dependency: SQLite validation and approved-example checking can materialize source rows before execution. Preventing reads before approval therefore requires changes to the validation/data-access paths as well as the UI.

| Issue | Priority | Recommended response | Main code boundaries |
| --- | --- | --- | --- |
| 1. Durable history exceeds cache expiry | Immediate privacy/correctness fix | Separate cache TTL from record retention; enforce expiry and deletion in every storage path | `persistence.py`, `api.py`, `App.tsx`, evaluation/report storage |
| 2. Decimal precision loss | Immediate correctness fix | Exact typed numeric contract across adapters, JSON, encryption, model formatting, UI and evaluation | `warehouse.py`, `contracts.py`, `access.py`, `api.py`, `persistence.py`, `evaluation.py`, web types |
| 8. Provider data handling | Immediate policy boundary | One mandatory outbound-model policy gateway; approved endpoint/model and data classifications | `models.py`, model config, all model callers, admin UI |
| 7. Final SQL approval | Required before sensitive-read approval can be claimed | Non-blocking approval states bound to the exact effective execution plan; no earlier business-row reads | workflow, validation, source adapters, jobs/API, `ReviewControls.tsx` |
| 3. Row-level restrictions | Release blocker for region/tenant-isolated use | Explicit inherited policies enforced at every protected base table, preferably database-native | `auth.py`, `access.py`, source adapters, policy configuration/admin UI |
| 4. SQLite copying ceiling | Required for larger local workloads | Metadata-only planning; direct scoped reads for supported queries; selective bounded handling of encrypted fields | `access.py`, `storage.py`, policy compiler, adapter capability checks |
| 5. Correlated model errors | Start early; required for any verified-answer claim | Deterministic metric contracts, independent reference results and appropriately labeled evidence | knowledge assets, agents/contracts, evaluation and answer rendering |
| 6. Serialized capacity | Required for a shared service | Release workers while waiting; then add durable dispatch, quotas and measured concurrency | jobs/persistence, API, workflow runner, connection/provider resource controls |

Do not solve capacity by immediately increasing the thread count. That would multiply materialization, provider requests and policy/session races while retaining the blocked-confirmation design.

## Shared design foundations

Create three explicit server-side contracts before changing the orchestration:

1. **Access context:** principal and source IDs, effective domain/table/column grants, verified tenant/region attributes, effective row predicates, PII permissions and policy version. The server supplies this context; questions and model output cannot grant access or set identity claims.
2. **Execution plan:** source/dialect, final rewritten SQL and typed parameters, security predicates/projections, output/resource limits, metric/schema versions and a canonical fingerprint. Display and approve this plan, rather than a candidate that an adapter later changes invisibly.
3. **Retention/egress policy:** what is stored, for how long, and which classifications may be sent to each approved model profile. Viewing PII, persisting PII and sending PII to an external provider are separate permissions.

Keep security decisions outside model reasoning. Existing read-only controls, parameter binding and single-use execution authorization remain in place.

## 1. Retention and deletion

### Changes

- Add indexed metadata outside encrypted snapshots: owner/source IDs, creation/completion/expiry timestamps, payload version and deletion state. Expiry and ownership must be checkable before decrypting a payload.
- Keep `job_cache_ttl_seconds` separate from `history_retention_seconds`. Return explicit `expires_at` values. Cache eviction must neither extend nor substitute for durable retention.
- Proposed starting defaults: 24-hour completed-history retention, no persisted raw result rows, and feedback attached to the query's lifecycle. These are recommendations, not current settings. Questions, SQL, parameters, answers and reasons can all be sensitive even when raw rows are omitted.
- Apply the same expiry decision to reads from memory and the records database. Read access must deny an expired record immediately; physical deletion may follow through a bounded cleanup batch.
- Run maintenance at startup and periodically, including when no new queries are submitted. A shared deployment needs a coordinated maintenance job rather than a cleaner in every API process.
- Add owner-scoped single/bulk deletion and administrator-managed retention settings. Extend CSRF and method/origin handling for any new deletion endpoints.
- Distinguish **Remove from this view** from **Delete saved query/history** in the UI. Attach feedback and pending approvals to query deletion. If reviewed feedback is promoted, retain a deliberately redacted independent artifact with its own policy; do not silently preserve the original record.
- Add a deletion generation/tombstone checked by every snapshot writer, feedback writer and worker completion. A late `finally` block, retry, cache fallback or feedback POST must not recreate a deleted/expired query. Retain only minimal non-content tombstone metadata for the necessary late-writer horizon.
- Give non-content security audit events a separate bounded retention policy. Do not purge accounts, access grants or domain definitions with query history. Cover managed evaluation reports and diagnostic artifacts too; ordinary evaluation JSON can contain rows and questions.

### Migration and limits

Version the schema and backfill timestamps from valid legacy snapshots. Produce a dry-run inventory of legacy records and proposed expiry actions. Quarantine records with missing/invalid timestamps for a bounded administrator-reviewed migration policy; never interpret missing expiry as unlimited retention. Do not purge existing data as a side effect of this planning task.

Specify database/WAL maintenance and backup expiration separately. Deleting a live row or using SQLite secure-delete is not a promise to erase existing backups, filesystem snapshots or storage-device remnants. Restoring a backup must reapply deletion/expiry metadata before serving history.

### Acceptance tests

Use a fake clock. Verify expiry without new submissions, across restart, with encrypted payloads and with stale cache entries. Test deletion racing worker completion/feedback, owner isolation, feedback cascade, cleanup retry, backup restoration and non-resurrection. Verify the UI communicates server deletion accurately.

## 2. Exact numeric handling

### Changes

- Preserve driver `Decimal` objects internally. Remove the `Decimal -> float` conversion and introduce one typed result/parameter codec used at every serialization boundary.
- Represent exact decimals as explicit decimal cells containing a canonical string and available precision/scale metadata. Encode integers outside JavaScript's safe range as typed integer strings too. Preserve NULL separately; do not guess that an arbitrary text string is a number.
- Example wire value: `{"type":"decimal","value":"9007199254740993.01000000","precision":38,"scale":8}`. Use ordinal column metadata where aliases repeat; support per-cell tags where source column types are heterogeneous.
- Apply this contract to API responses, job snapshots, encrypted records, result tokenization/restoration, model tool context, SQL parameters, evaluation reports and frontend types. Current JSON cloning and encryption also need the codec; otherwise merely retaining `Decimal` will introduce serialization failures.
- Bind exact decimal parameters through typed driver parameters. Do not first coerce them to float. Preserve exact strings through frontend rendering; avoid `Number(value)` or floating-point formatting that discards digits.
- Compare exact expected decimals using decimal/integer arithmetic with an explicit precision policy. Keep approximate tolerances only for declared approximate values. Reject non-finite or oversized exact values with an explicit result-quality error rather than silently converting to NULL or clipping a financial value.
- Exact transport cannot repair inexact SQL arithmetic. Certified financial paths must use exact source NUMERIC/DECIMAL or documented integer minor units; review currency scaling, division and rounding contracts. SQLite binary floating-point calculations must not be relabeled as exact decimals.

### Migration and acceptance tests

Version the wire/snapshot format. Old values already stored as floats cannot be reconstructed reliably; mark that provenance and require re-execution for exact results. Test large NUMERIC values, fractional cents, negative values, trailing zeros, NULL, scientific notation, large IDs, typed parameters, encrypted round trips and model-token restoration through browser parsing. Include a live PostgreSQL/MySQL decimal round trip before claiming connector precision is verified.

PostgreSQL documents exact NUMERIC arithmetic; JavaScript JSON parsing can lose numeric precision before client code handles it. See [PostgreSQL numeric types](https://www.postgresql.org/docs/current/datatype-numeric.html) and [MDN JSON.parse](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/JSON/parse).

## 3. Inherited row-level access

### Policy semantics

Make row security a distinct policy capability, not a domain-description convention. Source configuration declares tables as protected or explicitly global and defines trusted filter paths for region/tenant ownership.

For a protected table, the recommended composition is:

`hard tenant boundary AND an authorized row allowance AND every mandatory restriction AND NOT any matching explicit denial`

Multiple allowed regions within an allowance are ORed; alternative group allowances may be ORed. Mandatory restrictions intersect. An unrelated group must not remove a hard tenant boundary or an explicit denial. An empty/missing protected-table allowance means deny, not unrestricted access. Any unrestricted-row grant must be explicit and administrator-controlled. Keep the existing PII-deny precedence independent of these row rules.

### Enforcement

- Store verified user attributes and admin-managed group policies. Begin with a small typed predicate language (`equals`, `in`, authenticated-attribute references), not arbitrary SQL fragments. Missing claims, unknown columns, unsupported operators and unauthorized policy edits fail closed.
- Enforce predicates at each protected physical-table access before joins, aggregation, ordering or limits. Cover CTEs, aliases, subqueries, unions, self-joins, wildcard projection and queries that omit the obvious customer/tenant table.
- Declare ownership paths explicitly. For example, `order_items -> orders -> customers.region` needs an injected security predicate even when the user queries only `order_items`. Use tenant-qualified/composite keys where identifiers repeat between tenants. Do not infer ownership solely from foreign-key names or the LLM's chosen joins.
- Apply the same scope to previews, approved examples, validation probes, execution, summaries, saved results and any caches. Policy-revision changes invalidate pending approvals and reusable results. SQL filtering after results are fetched is not acceptable.
- Prefer PostgreSQL-native RLS for the first shared-service backend, using a non-owner, non-superuser runtime role without `BYPASSRLS`. Apply request identity in trusted transaction-local context and verify connection reuse/reset behavior. Do not assume enabling RLS constrains owners or privileged roles. An app administrator's intended bypass must be an explicit, audited application/database policy.
- For MySQL/SQLite, introduce a separately tested secured-view/predicate compiler. Until a source supports the required policy, reject access to protected tables; do not claim equivalent native RLS support or silently fall back to unrestricted reads.

An application rewrite must also be checked for backend-specific optimizer, function, error and timing behavior; wrapping a table or appending a WHERE clause is not automatically equivalent to a database security barrier. Reserve security-parameter names so model-authored parameters cannot replace verified identity values.

### Acceptance tests

Create multi-tenant fixtures with overlapping customer/order IDs, two regions, shared global dimensions, NULL ownership, composite keys and conflicting groups. Assert that all bypass query forms return only authorized rows or are rejected. Test revocation while queued, awaiting approval and executing; pooled connections must not leak prior identity. Test under the actual database runtime role, including owner/bypass-role misconfiguration. Enable `row_policy_support` only per verified adapter capability.

PostgreSQL's owner/privileged-role behavior is documented in [Row Security Policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).

## 4. Scaling the protected SQLite path

Separate planning/compilation from reading business data. Build a metadata-only validation catalog or use a proven schema/type resolver; approved-example compilation must not copy source rows. Validation results that require actual data become explicitly approved execution probes.

For supported non-encrypted queries, use a read-only source connection with deterministic, policy-injected table projections/predicates. Preserve the authorizer, function restrictions and limits; a rewritten query must not permit direct access to unprotected base tables. Query only referenced/required tables and let the source use its indexes instead of copying the entire domain union for every validation and execution.

Encrypted local fields require a separate capability path. Push down safe row/security predicates first and decrypt only eligible data locally. Filtering, joining, sorting or aggregating encrypted fields may still require a bounded local view. Do not expose a general decryption function to model-authored SQL, put plaintext in a shared cache or promise transparent indexed querying of ciphertext. Reject unsupported operations clearly.

For larger shared workloads, provide a deliberate migration path to a secured analytical database rather than raising memory caps indefinitely. Do not silently copy a user's database to another service.

Benchmark small selective queries over large datasets, validation without row scans, approved encrypted-field reads, cancellation, memory and source-snapshot consistency. Preserve existing isolation tests. Until replacement paths pass, keep and document the current fail-closed limits.

## 5. Stronger semantic evidence

A different prompt or a second model is an additional opinion, not independent verification. Keep model review as a labeled advisory check and add evidence the model cannot manufacture:

- Versioned metric contracts: measure, grain, eligible statuses, time basis/timezone, half-open date boundaries, currency/unit, NULL behavior, permitted joins/cardinalities and rounding.
- A certified path for approved metrics using reviewed parameterized SQL/structured expressions. Keep flexible generated SQL available, but label it as generated and unverified unless it meets the appropriate evidence criteria.
- Deterministic checks for contract coverage, join cardinality/fan-out risk, duplicate counting, incompatible units and missing filters. An AST warning is not proof of arbitrary SQL semantics.
- Reviewed reference queries and held-out fixtures with many-to-many joins, partial payments/refunds, missing values, time boundaries and per-identity row scopes. Add invariant/metamorphic checks where valid: splitting an independent child row should not inflate an unrelated metric, for example.
- Independently calculated expected results; do not use the same generation model to create its own correctness labels. Track execution correctness, explanation faithfulness, infrastructure failures and uncertainty separately.
- Render essential numbers/units from typed executed results or verified templates. Let prose explain those facts; do not rely on free prose as the numerical record.

Optional separate reviewer models/providers can be configured after egress approval, but must not increase the UI's verification claims on their own. Data-reading diagnostic probes must be included in the approval manifest and read scope. If results violate a contract, stop publication or return a clearly qualified outcome rather than silently declaring success.

Gate certified-answer labels on reviewed contracts and reference results. Keep a separate general-generation score; a high score on one fixture does not certify unseen schemas.

## 6 and 7. Durable phases, capacity and final SQL approval

### Non-blocking lifecycle

Replace the `threading.Event.wait()` workflow with durable, versioned phases:

`queued -> planning -> awaiting_tables -> generating -> validating -> awaiting_sql_approval -> executing -> explaining -> completed`

Terminal alternatives include cancelled, expired, failed, policy_changed and interrupted. Waiting consumes no workflow worker and holds no database transaction/connection. Store phase artifacts, not live model objects, encryption keys, database handles or reusable execution capabilities. On restart, revalidate saved plans and access; require fresh approval where previous state cannot be established safely.

### Approval contract

The UI shows the exact effective SQL, typed filter values, source, domain/table scope, security filters, PII treatment, limits and any validation-probe queries. Sensitive literals still follow the viewer's permissions. SQL edits are optional future functionality; accepting edits would require full validation and a new plan revision.

Bind approval to the principal, source, canonical plan hash, typed parameters, schema/metric versions, access/row/PII and egress-profile policy versions, expiry and job revision. Consume it atomically once, immediately before execution after checking current permissions and source capabilities. Changing any bound element requires new approval. Adapter rewrites and resource caps must be applied before the plan is approved; no unseen SQL rewrite may change what is read afterward.

Before approval, allow only policy-approved metadata access and metadata-only local compilation. Do not run the SELECT, `EXPLAIN ANALYZE`, validation probes or the current SQLite materialization. Do not assume ordinary remote EXPLAIN is data-free: functions or optimizer behavior can execute work during planning. Live compilation must have a verified data-free capability or run as part of the approved manifest. Any unavoidable sensitive diagnostic read needs its own explicit approval. Displaying a preview is not itself authorization.

Expose owner-scoped approve/reject/cancel endpoints with CSRF protection, idempotency, state/version checks and expiry. Start with final SQL approval required for all UI reads; a later administrator policy may allow approved low-risk templates to auto-run. Read-only execution controls remain mandatory in both modes.

### Capacity after the lifecycle is sound

Introduce a durable job store/dispatcher with atomic leases, heartbeats, fencing tokens and compare-and-set transitions. Keep SQLite for the local single-process profile initially; use an appropriate transactional shared store for multi-process dispatch. Select a separate broker only when measured queue requirements justify it.

Add configurable global and per-user concurrency, fair queues, bounded waiting jobs, per-source connection/query limits and provider token/request budgets. Begin conservatively and increase from measured behavior. Multiple HTTP workers must not each create an independent unsynchronized queue or duplicate execution state.

Cancellation must reach the model/database operation where supported; cancelling an HTTP task or future does not prove the database query stopped. Do not automatically retry a lease-lost/unknown-completion SQL execution. Mark it interrupted, cancel the source query if possible and require a new request/approval for a rerun. This design targets one authorized active execution and fenced result publication, not an unjustified exactly-once guarantee across remote databases and process failures.

Test that an abandoned confirmation does not block other users, duplicate approvals cannot execute twice, stale workers cannot publish, restarts do not replay approvals, revocations win and quotas hold under load. Record queue wait, active execution, provider wait, database latency and memory separately. Define concurrency/latency targets from representative workloads before choosing worker counts or dates.

## 8. Model-provider data policy

Create one mandatory outbound-model gateway covering intent, schema selection, SQL generation, review, response, domain suggestions, repair prompts and evaluation calls. No role should bypass it by directly constructing a provider client.

Use administrator-approved model profiles with explicit endpoint/model allowlists, permitted data classifications, allowed contexts, maximum payload budgets and approval metadata. Enforce the destination at transport/egress level too, including redirects, so an approved profile cannot silently send data to another host. Classify question text, schema/descriptions, examples, errors, SQL/parameters and results separately. User/group permission to view decrypted PII does not grant permission to send it to a provider. Unknown profiles or disallowed classifications must block before the first network request; avoid silent provider fallback.

Run local checks before intent generation. Detect/redact obvious emails, phones, credentials and configured identifiers where approved; keep any reversible token map local, request-scoped and short-lived. Do not use an external detector before checking egress permission. Minimize metadata and sanitize errors/retry context as well as result cells. Apply the checks again when payloads or policy versions change.

Detection is imperfect: a regex or local classifier cannot guarantee recognition of sensitive free text. High-assurance sources should require an approved local model or restricted structured inputs, or block the request. Do not present a warning checkbox as a bypass of organizational policy. Organizational approval of provider retention, training use, residency and terms must be recorded; API compatibility alone does not establish any of those properties.

Show the active approved profile and handling mode in the UI. Audit policy decisions using IDs, classifications and reason codes without retaining raw rejected prompts or sensitive literals. Include logs, traces and evaluation reports in the retention design. Test with a recording fake transport that asserts no outbound bytes for blocked requests, and inspect every role's actual outgoing payload for forbidden test markers.

## Delivery sequence and release gates

| Phase | Deliverables | Gate before proceeding |
| --- | --- | --- |
| A: stop silent leakage/loss | Retention/expiry/deletion, exact numeric codec, outbound provider policy; clarified UI labels | Encrypted migration/read/delete races pass; exact end-to-end numeric round trips; blocked prompts produce zero network calls |
| B: govern reads | Versioned execution plans, metadata-only validation and non-blocking table/final-SQL approvals | No business-row reads before approval; exact plan consumed once; cancellation/revocation/restart tests pass |
| C: enforce row isolation and improve source access | Group row-policy contracts; PostgreSQL-native reference implementation; secured SQLite/MySQL capability paths; selective local reads | Real database tenant/region bypass suite passes; unsupported adapters deny protected tables; memory/selectivity benchmarks meet agreed targets |
| D: measured shared-service capacity | Durable dispatch, leases/fencing, quotas, cancellation and observability | Multi-user load and failure-injection tests show isolation, fairness and no approval replay/duplicate active dispatch |
| E: stronger answer claims | Certified metric templates, deterministic checks, independent references, faithful answer rendering and broader live evaluation | Publish measured outcomes and only enable verification labels for the evidence actually established |

Start the metric contracts and fixture design during Phase A; Phase E is the release gate for stronger answer claims, not a reason to defer correctness work. Do not market region/tenant isolation before Phase C, sensitive-read approval before Phase B, or shared-service capacity before Phase D.

Each phase should be a small series of independently reviewable changes: contract/schema migration, backend enforcement, API/UI changes, adversarial/compatibility tests, then documentation. Keep current functionality as a compatibility baseline; do not weaken existing permission, PII or read-only checks to obtain passing tests. Dates/effort estimates should follow the policy/compiler spike and live connector results rather than a guessed schedule.

## Decisions to settle before implementation

- Local-only versus shared-service deployment profile and target load.
- Retention duration and allowed history payloads, including reviewed feedback, reports, backups and security audit metadata.
- Which sources/tables are tenant/region protected versus global; trusted identity attributes and explicit administrator bypass rules.
- Whether final SQL approval is mandatory for all reads or policy-selected cases; whether a second person must approve sensitive reads.
- Approved provider profiles and classifications, plus the treatment of uncertain sensitive free text.
- Metrics eligible for certified templates and the required independent evaluation evidence.

The plan above recommends conservative defaults but does not assume these organizational decisions have already been approved.
