# Extending protected queries to future connectors

The application keeps shared governance separate from database-specific execution. Adding a profile for a [registered engine](WAREHOUSE_CONNECTORS.md) reuses its existing adapter; it needs reviewed domain/table, PII, row-policy and provider-classification mappings. A new engine/dialect requires a tested adapter implementation, not just a new connection URL.

## Shared responsibilities

Keep these controls common to all connectors:

- Server-authenticated principal, domain/table scope, PII permissions and trusted row attributes. Questions/model output cannot grant access.
- Versioned, source-keyed business/PII/row/provider configurations. Policy changes invalidate access stamps, approvals and recovery artifacts.
- Metadata-only planning, reviewed examples, generation and column pruning. Private physical dependency schemas stay outside model/browser plans.
- Canonical approval manifest: source, effective SQL/typed values, exposed schema, security projection/predicates, policy version, limits and execution path/capabilities.
- Fresh permission/plan/expiry checks, single-use execution capability and durable pre-execution marker.
- Bounded result format and exact decimal/large-integer codec; response tokenization, scope disclosure and provider-policy gateway.
- Owner-scoped history/feedback/checkpoint retention, deletion and explicit safe-review restoration. No persisted execution tokens or automatic replay of uncertain work.

## Adapter boundary

The [mapped SQL Server adapter](SQLSERVER_CROSS_DATABASE.md) is an opt-in same-instance extension. Keep stable logical IDs as the permission/policy keys and resolve only operator-registered physical catalog/schema/table identities. Expose permitted physical identities to schema selection, include them in approval, apply projections/row policies before joins, and reject linked servers or unknown mappings. Do not generalize this capability to another engine without testing its catalog, identifier, grant and metadata semantics.

Adapters may implement metadata-only `table_options(tables)` returning `{id, database, schema, table}` entries for registered logical IDs. The access wrapper checks requested IDs against the current principal's permitted tables before exposing locations. Review proposals carry this optional metadata for grouped selection, and recovery rebuilds it from current configuration. It must not sample rows or expose unconfigured/unpermitted objects. Adapters without the hook retain flat selection.

| Interface | Requirement |
| --- | --- |
| `catalog()` / `schema(tables)` | Return trusted metadata for permitted regular objects without sampling business rows. Identify dialect, columns, primary keys and accepted relationships. |
| Protected access wrapper | Enforce PII projection and effective row policies at every base-table reference or a proven native boundary, before filtering/aggregation visible to the user. |
| `plan(sql, parameters, tables)` | Perform data-free checks. Return `referenced_columns`, `effective_sql`, optional `effective_parameters`, `execution_mode` and a meaningful `read_scope`. Reject unsupported syntax/capabilities. |
| `execution_capabilities()` | Declare dialect, protected direct-read support, local PII decryption support and enforcement mechanism. Capability flags are promises requiring implementation/tests. |
| `execute(...)` | Execute only the approved protected plan under current source grants and limits. Return bounded columns/rows/truncation plus actual SQL/parameters where rewritten. |
| Access checks / lifecycle | Recheck current identity/policy before sensitive work and delivery. Close transactions, connections and clients; maintain policy/session isolation in pools. |

Current modes are `direct` and `materialized` (bounded local processing). The manifest fingerprints the selected mode and capabilities. Keep private execution artifacts on the server. Replanning after approval must not silently change the executed query, protection or limits.

Adapters offering `selective_local_copy` must include the actual source SELECTs/values, transferred columns, decryption dependencies and row-marker operations in `copy_scope`. Predicate movement must preserve null, join, subquery, type and collation semantics. Decrypt only required values after source access restrictions. SQLite's implementation is documented in [COPY_REDUCTION.md](COPY_REDUCTION.md); its SQL rewriting is dialect-specific, while approvals/policies/recovery remain shared.

Register a new source kind explicitly in `SourceRegistry` and an appropriate protected wrapper in `protected_database`. Registered protected wrappers cover SQLite plus PostgreSQL, MySQL, SQL Server, Oracle, Snowflake, Redshift and BigQuery. Unknown/unregistered objects fail closed. Implement a row-predicate compiler/native policy binding for the new dialect; the versioned policy semantics and subject resolution should remain shared. Do not load arbitrary modules or SQL fragments from user configuration.

## Required connector evidence

Adapters claiming `bounded_local_pii` must use approved `PIILimits` and a per-attempt aggregate budget across tables. Bound source bytes, copied plaintext, rows, envelopes, decoded scalar cells, decryption count and preparation time; reject oversized ciphertext before decrypting/parsing. Require a tested `decrypt_bounded(value, aad, limits)` cipher interface, close resources on failure, return no partial results and stop automatic repair for bound failures. Include effective `pii_limits` in the approval manifest and policy/cache identity. SQLite implements this contract; new engines must test their own storage, cancellation and encryption behavior. See [BOUNDED_PII.md](BOUNDED_PII.md) for cooperative timing and memory-accounting limits.

Use a separate non-sample schema with overlapping tenant IDs and composite relationships. Test hidden PII in expressions/predicates/stars, every join/CTE/subquery/union base table, NULL ownership, missing/conflicting attributes and unmapped tables. Include source/type/collation semantics, exact numeric round trips, parameter collisions and unsupported functions/namespaces.

Prove no business-row access before approval; fingerprint the final effective plan, including server rewrites. Test policy/schema changes during waits and execution, concurrent users, pooled-identity reset, cancellation/expiry, safe restart restoration and no replay of uncertain reads. Run under the actual intended database runtime role and verify read-only/native-policy behavior, then measure representative limits/performance.

SQLite has end-to-end local tests. Remote engines have local projection/rewriting and mocked lifecycle/job tests; live server/runtime-role verification remains pending. BigQuery also binds billed-byte and location limits to approval. Other warehouses are not automatically supported. Database-native RLS protects other clients only when configured/tested there; application projections govern this application's authenticated query path. CLI/evaluation/direct adapter calls remain trusted operator interfaces.
