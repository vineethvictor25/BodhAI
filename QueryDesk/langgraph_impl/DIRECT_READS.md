# Direct protected database reads

Implemented on 14 November 2026. This increment applies column/row protections during supported SQLite reads rather than copying business rows into an in-memory database first. It uses the shared access, approval and recovery contracts; it contains no sample-table rules. PostgreSQL/MySQL retain their existing source-side projections and row predicates.

## Execution paths

| Query | SQLite path |
| --- | --- |
| A member queries permitted non-PII columns | Direct read through a protected projection and configured row predicates. Classified PII columns are absent. |
| A PII-permitted user queries non-PII columns or COUNT(*) | Direct read when metadata confirms no classified PII column is referenced. |
| A PII-permitted user references a classified PII column in SELECT, filters, grouping, joins or ordering | Existing bounded local path, preserving local decryption semantics. |
| Unsupported/unsafe SQL or an unregistered connector | Reject; never fall back to an unrestricted connection. |

PII-reference detection uses SQLite's metadata-only compilation against empty permitted schemas. It does not sample source values. The PII path is conservative even for currently plaintext fields. [Selective local copying](COPY_REDUCTION.md) now transfers required columns/keys from referenced tables and applies eligible single-table non-PII conditions at the source. It retains the 100,000-row/20 MB transfer ceiling; broad/complex PII queries can still copy all scoped rows they require. Streaming decryption remains future work.

Direct reads have no local-copy ceiling. They still have the existing result-row, text-cell and serialized-output limits and SQLite execution deadline. This is not a promise of unlimited query size, memory, latency or concurrency. A single workflow segment still executes at a time. Large joins, sorts and operating-system I/O require representative performance testing.

## Direct read boundary

1. Compile the original SQL against empty permitted schemas, recording referenced columns and rejecting hidden PII reads.
2. Parse supported SQLite SELECT/CTE/set queries and replace every physical table reference with an explicit permitted-column projection. Add configured row predicates inside each projection before joins/aggregation. Bind policy values separately from SQL.
3. Qualify physical source/policy-helper tables in `main`, preserving CTE aliases without permitting CTE names to replace trusted helper dependencies. Reject table functions, writes, other namespaces, unsupported renderings and policy-parameter collisions.
4. Include the effective SQL, actual parameters, read mode, adapter capabilities, policy restrictions and source-schema fingerprint in the approval manifest. Physical dependency schemas remain private to the server; public/model-facing plans contain only permitted metadata.
5. After approval, use the cached prepared plan, open a read-only source connection and a read transaction, and verify physical tables/columns still match that plan. Do not accept a table changed into a view or silently recompile against changed columns.
6. Enforce query-only/trusted-schema settings, a physical table/column and function authorizer, SQL/value budgets and the VM deadline on that same connection. Fetch only a bounded result. Recheck access before returning it.

The native authorizer complements rewriting; it does not trust a view/CTE name as proof of authorization. Caller namespace qualifications are parsed and rewritten. Policy-only helper tables can be used internally for inherited row restrictions without becoming queryable user tables. PII permissions, row filters, provider data policy, result tokenization and history ownership remain independent controls.

## Approval, UI and recovery

The final review panel labels **Direct protected read** or **Bounded local PII processing** and describes the read scope. It shows the actual rewritten SQL and bound values for direct reads. Mode/capability/schema changes change the manifest fingerprint, so restored reviews and resumed approvals must match the current plan.

Existing execution markers are persisted before the execution stage. Direct reads do not enable automatic replay after a crash. Safe reviews can be restored with fresh approval; running/uncertain executions remain interrupted. No live connections or prepared-plan objects are placed in durable checkpoints.

`/api/config` exposes the selected protected adapter's `execution_capabilities`. A source must use an explicitly supported adapter; a plugin cannot gain access merely by declaring `kind: postgresql`. The detailed extension contract is in [CONNECTOR_CONTRACT.md](CONNECTOR_CONTRACT.md).

## Dependencies and verification

Local encrypted PII preparation has separate configurable resource limits included in SQL approval. See [BOUNDED_PII.md](BOUNDED_PII.md) for aggregate/cell budgets, cooperative preparation deadlines and safe failure. Direct non-PII reads retain their existing limits.

SQLGlot is now a core dependency, pinned in the reproducible lock file. It is already installed in this workspace. Recreated virtual environments obtain it through the normal setup script. Unsafe or unsupported dialect rendering raises an error rather than dropping syntax silently. Named SQLite CTE columns are preserved and covered by a result check.

Tests aggregate more than 120,000 permitted rows without opening the local-copy path, with a source containing over 20 MB of text. Other checks cover row/PII enforcement, CTE context spoofing, qualified names, self-joins, encrypted PII fallback, metadata-only planning, private-schema exclusion, native timeout interruption, schema changes after planning, result limits and unknown-adapter rejection. Existing row-security, approval, encrypted-record and recovery tests remain in the suite. This is behavioral evidence, not a workload benchmark or live warehouse certification.

Final verification: 144 backend/API tests and 21 frontend tests passed; the production build passed and dependency checks were clean. Additional targeted connector checks passed after tightening adapter registration. No live model-provider calls were made.
