# Final SQL approval before application data reads

For [mapped SQL Server cross-database queries](SQLSERVER_CROSS_DATABASE.md), table confirmation uses registered logical IDs while the SQL proposal shows expanded physical `database.schema.table` references, protected projections and row filters. Mapping/schema/protection changes invalidate the approval fingerprint; linked-server and unregistered paths remain rejected.

Implemented on 14 November 2026 as the first increment after [Phase A](PHASE_A.md), addressing issue 7 in the [hardening plan](HARDENING_PLAN.md). Subsequent increments add [non-blocking reviews](NON_BLOCKING_REVIEWS.md) and [safe review-checkpoint recovery](RESTART_RECOVERY.md). Restored SQL always requires a new approval; running or uncertain executions are not replayed.

## Browser workflow

1. Ask a question and confirm the proposed source tables.
2. The agents retrieve metadata/examples, generate SQL and review it using metadata-only planning.
3. The UI pauses in `awaiting_sql_approval` and displays the effective SQL, bound parameter values, confirmed tables, returned-row limit, execution time budget and scope of permitted reads.
4. Choose **Approve SQL and run** or **Cancel without reading data**. Merely seeing SQL or confirming tables does not approve execution.
5. On approval, the server rechecks the account's current permissions, job retention deadline and execution-plan fingerprint before executing. The response then explains the executed results.

SQLite planning compiles against newly constructed, empty tables derived from permitted metadata. It does not copy source rows or decrypt source fields. Approved-example retrieval uses this same planning boundary. Warehouse planning parses/rewrites SQL locally and retrieves metadata; it does not run a source EXPLAIN or SELECT against business tables. Source compilation errors can therefore still appear after approval.

Metadata discovery is allowed before approval. This is not a promise of zero database communication: configured warehouses use their metadata interfaces, and SQLite reads schema metadata. It is also not an approval system for administrator maintenance commands, evaluations or direct CLI queries. Those remain trusted operator activities. The browser API always supplies the final-approval callback to the production workflow.

## What is approved

A canonical server-generated manifest binds effective SQL, typed parameter values, source ID, permitted schema/projection, confirmed tables, encryption-policy signature, row limit, execution timeout and disclosed read scope. The API adds a fresh version, deadline and SHA-256 fingerprint. The server supplies identity and permissions; model output cannot approve execution.

Displayed effective SQL includes adapter rewrites and policy values. Supported SQLite non-PII reads now use [direct protected source queries](DIRECT_READS.md), without business-row copying. Classified-PII queries retain a bounded local view that can read permitted columns/rows of confirmed tables before applying the SELECT's filters. The panel identifies the path and read scope. Only that materialized path retains the 100,000-row/20 MB ceiling. Execution mode, capabilities and available source-schema fingerprints are part of the approved manifest.

[Selective copying](COPY_REDUCTION.md) now adds the source SELECTs, values and transferred/decryption columns to the approved `copy_scope`. Only required data is transferred, and eligible simple non-PII predicates can reduce rows at the source. Join/subquery/encrypted conditions remain local. The complete original query is still evaluated after copying/decryption, and policy-only dependency schemas are checked before any transfer.

`POST /api/queries/{id}/approve-sql` accepts only `version`, `fingerprint` and `decision` (`approve` or `cancel`). It requires the query owner, a valid authenticated session and CSRF protection. Even an administrator inspecting somebody else's query cannot approve it. Wrong versions/fingerprints, late decisions, replays and permission changes cannot authorize execution. Each repaired SQL attempt receives a new approval version, even if the regenerated SQL is identical. The existing single-use internal execution capability is still required as well.

The review deadline is the earlier of the confirmation timeout (normally ten minutes) and the job's durable history expiry. Rejection, expiry or deletion prevents execution. Decisions and the reviewed manifest are retained under the query's existing history retention/encryption policy. Sensitive-content policy failures scrub those artifacts along with the rejected question. After restart, safe review checkpoints can be restored explicitly under their original deadlines; old approvals are never resumed.

Approval permits execution-time compilation and protected-view materialization as well as the final read. It does not prove the business meaning of the SQL, nor can cancellation undo a read already started after approval. Timeout controls retain the documented adapter limits; the SQLite VM budget does not bound all materialization or operating-system work.

## Remaining limits and next step

Waiting releases the existing single workflow worker through [non-blocking review segments](NON_BLOCKING_REVIEWS.md). Resumes recheck permissions, deadlines and the plan fingerprint while preserving budgets. [Restart recovery](RESTART_RECOVERY.md) renews review approval, and [row security](ROW_SECURITY.md) adds per-table restriction previews and policy-bound fingerprints. In-flight/distributed recovery, live warehouse policy verification, larger SQLite workloads and independent semantic verification remain separate work. No provider was approved, live provider request made or legacy history purged for these increments.

Automated checks cover no materialization/execution after rejection, real approved execution, changed-plan rejection, repair reapproval, confirmed-table-only copying, warehouse planning without source EXPLAIN, API ownership/CSRF/version/replay/timeout handling and the explicit UI action. Live PostgreSQL/MySQL verification and independent security review remain pending.
