# Selective copying for local PII processing

Implemented on 14 November 2026, following [direct protected reads](DIRECT_READS.md). Non-PII queries already avoid business-row copying. This increment reduces the remaining SQLite local PII work without bypassing approval, row policies or decryption checks.

## What is reduced

- Only tables actually referenced by the query contribute business rows. Unused confirmed tables have empty local schemas and are not scanned for values.
- Only referenced columns are transferred, plus the full primary key needed to authenticate/decrypt requested PII. Unused PII fields are not decrypted.
- A table needed only for row multiplicity can contribute NULL row markers instead of unused field values. Joins/counts retain their duplicate/row counts.
- Eligible non-PII conditions on a simple single-table query filter source rows before transfer. For example, an email lookup by customer ID copies that customer's key and email rather than every customer's contact data.

Metadata-only SQLite compilation supplies column references, including join/filter/group/order requirements and wildcard expansion. The local schema still contains the permitted column names; unneeded values are not transferred. The complete original SQL is then evaluated locally after required PII decryption. No original source DDL/defaults/triggers are executed in the local database.

## Conservative source predicates

Only top-level AND conditions from a single SELECT with one base table are eligible. Supported forms are direct non-PII column comparisons (`=`, `!=`, `<`, `<=`, `>`, `>=`), bounded literal/parameter IN lists, BETWEEN, and IS NULL. Values remain bound parameters, not interpolated SQL. Strings, NULL and signed 64-bit integer constants are supported; other expressions remain local.

No predicates are moved across joins, CTEs, scalar/correlated subqueries or set operations. Conditions involving PII, OR/NOT expressions, functions or unsupported values remain local. Explicit pseudo-rowid use disables source-predicate reduction because narrowing a copied table could change its local row numbering. There is no source LIMIT, OFFSET, grouping or ordering reduction.

The existing local tables have no declared affinity and use BINARY comparison by default. Source predicates deliberately remove source-column affinity with unary `+` and use explicit BINARY collation, preserving those local semantics. Otherwise a source TEXT affinity or NOCASE collation could drop rows that the local query would retain. These predicates may not use every source index; this is a reduction in transferred/decrypted data, not a universal latency guarantee.

Row-security predicates remain an additional AND restriction at the source. They continue to use their configured source type/collation semantics. Query-predicate reduction cannot broaden that security boundary. Policy-only helper tables are not copied merely because they are needed for row filtering.

## Approval and access controls

The approved manifest includes `copy_scope`: source SELECTs, bound values, transferred columns, decryption columns and any row-marker operation. The UI displays a **Source copy scope** section. The copy plan, source-schema fingerprint, read mode, capabilities and row policies all participate in the existing approval fingerprint.

Before copying, the server opens a read transaction and checks all physical dependency schemas, including policy-only parents, against the prepared plan. A changed table/view/column layout requires a new review. Private physical schemas remain server-only; the model/browser receives permitted planning information, not the private execution object. No source rows are read during generation or review.

The row/domain/table/PII checks, policy-change revocation, periodic access checks, result limits and execution marker remain in force. A source restriction or copy-scope change invalidates restored SQL approval. The durable checkpoint still contains planning artifacts and counters, never plaintext result rows or reusable execution capabilities. Unknown connectors still fail closed.

## Remaining limits and extension

The local PII path remains capped at **100,000 transferred rows / 20 MB of plaintext copied values** across copied tables. [Bounded encrypted PII handling](BOUNDED_PII.md) adds source-byte, per-cell, decryption-count and preparation-time limits, configurable in `.env`. A selective lookup can succeed against a much larger source table, but a broad PII query may still hit a limit. This is not streaming decryption or arbitrary join-predicate optimization. The preparation deadline is separate from the final SELECT deadline and cannot preempt operating-system I/O or an atomic cryptographic operation.

The implementation uses metadata and per-source mappings, not sample-table names. `selective_local_copy` is an adapter capability. PostgreSQL/MySQL already use source-side projections; this change does not add remote ciphertext decryption. Future engines must implement/test their own compatible strategy through [CONNECTOR_CONTRACT.md](CONNECTOR_CONTRACT.md), particularly type/collation semantics. SQLite-specific comparison rewriting must not be reused blindly for another dialect.

## Verification

Tests verify one-record PII decryption in a table above the old row-copy ceiling, only requested PII/key columns, omission of unreadable unused encrypted fields/tables, source NOCASE/affinity differences, local encrypted conditions, OR handling, outer-join NULL semantics, self-subquery counts, row-marker join multiplicity, pseudo-rowids and schema changes to policy-only dependencies. Existing approval, revocation, encrypted storage, row security and restart-recovery checks remain in the full regression suite. No live provider calls or remote-warehouse performance claims were introduced.

Historical verification for the selective-copy increment: 154 backend/API tests and 22 frontend tests passed, the production frontend build passed, and dependency checks were clean. Additional targeted checks passed after aligning the generated source-SQL budget with the prepared plan.

For the latest complete regression count, see [README verification](README.md#verification).
