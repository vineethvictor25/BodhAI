# Bounded local encrypted PII handling

Classified PII referenced by a permitted SQLite query retains local decryption and full local SQL evaluation. [Selective copying](COPY_REDUCTION.md) limits required tables, columns and eligible source rows first. Row restrictions still apply at the source; no business rows are read before final SQL approval.

## Configuration

These `.env` settings apply across all copied tables in one execution attempt:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `PII_MAX_COPY_ROWS` | 100000 | Transferred rows |
| `PII_MAX_COPY_BYTES` | 20000000 | Aggregate values inserted into the plaintext local copy |
| `PII_MAX_INPUT_BYTES` | 40000000 | Aggregate selected source values, including ciphertext and decryption keys |
| `PII_MAX_ENVELOPE_BYTES` | 1000000 | Encoded encrypted cell, before base64 decoding |
| `PII_MAX_CELL_BYTES` | 1000000 | Serialized encrypted payload and decoded scalar cell size |
| `PII_MAX_DECRYPT_CELLS` | 500000 | Requested PII cells processed, including plaintext and NULL cells |
| `PII_COPY_TIMEOUT_SECONDS` | 10 | Local preparation deadline |

All settings must be positive. Row, byte and cell-count settings can be lowered but cannot exceed the defaults; the preparation timeout can be changed up to 60 seconds. Invalid settings stop startup. Restart the backend after changing `.env`. Limits form part of the PII policy identity and SQL approval fingerprint; old reviews cannot execute with changed limits. The SQL review panel displays effective limits.

Byte counts measure payloads, not total Python/SQLite memory usage. Strings use UTF-8 bytes; numeric values use their textual size. The cell limit also applies to serialized JSON inside authenticated ciphertext, so JSON overhead can make a value exceed the limit even when its decoded text is smaller.

## Enforcement and failure

Before decrypting each row, the adapter checks aggregate row and source-byte budgets. Before each PII cell, it checks time and cell-count budgets. Encrypted envelope length is checked before base64 decoding; AES-GCM ciphertext length supplies a plaintext-size check before decryption or JSON parsing. Only scalar SQLite values are accepted. Authentication failures, wrong keys, malformed ciphertext and unsupported decoded values fail closed.

Plaintext-copy size is checked before inserting each row. One deadline covers preparation, with checks between tables, rows and cells plus a source SQLite VM progress handler. Final local SELECT execution retains its separate existing VM deadline. The preparation timeout is cooperative: it cannot interrupt operating-system I/O or an individual cryptographic operation in progress. These are resource safeguards, not a strict process-memory or wall-clock guarantee.

A limit failure closes the private database and source transaction, returns no partial result and ends the attempt. It does not trigger automatic SQL repair or a result-explanation model call. The user can narrow the question and review a new attempt. The local copy uses an in-memory database and `temp_store=MEMORY` for SQLite temporary work; operating-system swap and crash dumps remain deployment concerns.

Existing domain/column permissions, row-policy checks, schema checks, approval expiry, revocation and result limits remain enforced. Keys never enter approval manifests or model context. This feature does not enable encryption or migrate data automatically. History/report/backup encryption has separate retention and storage behavior; these limits govern local query-cell processing.

## Connected databases

Limits use shared `PIILimits`/`PIIBudget` contracts and source metadata, with no sample-table assumptions. SQLite declares `bounded_local_pii` and requires a cipher implementing `decrypt_bounded(value, aad, limits)`. An adapter must provide equivalent tested controls before claiming that capability; an unsupported cipher fails during planning. PostgreSQL/MySQL source projections do not acquire local ciphertext decryption through these settings. New engines require implementation and runtime tests as described in [CONNECTOR_CONTRACT.md](CONNECTOR_CONTRACT.md).

## Verification

Ten focused tests cover row/input/envelope/cell/count/copy/time limits, corrupt ciphertext, wrong keys, non-scalar payloads, connection cleanup, in-memory temporary storage, configuration validation, approval fingerprints and terminal workflow failure. Tests use temporary synthetic databases and no live model requests.

Historical verification for the bounded-PII increment: 164 backend/API tests and 23 frontend tests passed, the production frontend build passed, and the dependency check found no broken requirements. Live warehouse encryption/runtime verification remains pending.

For the latest complete regression count, see [README verification](README.md#verification).
