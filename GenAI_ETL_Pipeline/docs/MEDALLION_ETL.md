# File ETL, Medallion layers and loading — release 1.5

This is an executable, bounded local reference implementation. It does not deploy a distributed lakehouse or connect to a cloud warehouse merely because a cloud is selected. See DESTINATIONS.md and ENGINEERING_COVERAGE.md for boundaries.

## Try the complete workflow

1. Open the application and choose **Medallion ETL → Stage four-format order example**. This stages real CSV, nested JSON, Parquet and pipe-delimited TXT. It does not run a model or publish data.
2. In Pipeline studio, review the order-line grain, owner, mappings, transformations, privacy policies, load strategy and quality checks in the executable JSON specification.
3. Choose **Preview Silver & Gold**. It performs transformations and checks without writing pipeline outputs. Gold preview covers this batch only.
4. Select **Local workspace / SQLite**, apply the configuration, and run the reviewed pipeline.
5. In Medallion ETL, inspect Silver, Gold, STTM and excluded rows. Download successful Silver/Gold as JSON, CSV or Parquet.

The fixture has 8 input rows: 5 accepted lines, 1 invalid quantity quarantined, 1 cancelled line filtered, and 1 older duplicate superseded. Gold revenue is 710 USD. Running the fixture again upserts the same five business keys; it does not double the revenue.

The Overview page's **Published warehouse** panel lets you select a target, inspect its current rows, and run a read-only validation over the full target. It checks stored JSON records, contract columns and types, unexpected columns, and primary-key consistency without returning record values in the validation report.

## Source formats and multi-file ingestion

The source picker accepts multiple files, up to 20 per UI action. Each is uploaded independently, with per-file results: an invalid file does not erase successful uploads. Limits are 5MB encoded and 25,000 nonempty object records per source. The application is synchronous and memory-bounded; this is not a distributed reader.

After a successful upload, Pipeline Studio automatically populates an editable draft specification from the first uploaded source's flattened columns when the current specification has not been manually edited or explicitly chosen. Source names become target aliases; scalar numeric values are conservatively inferred as integer/number, and other values remain strings. A complete unique identifier is preferred as the required primary key; protected fields are excluded from key selection. If no unique key is detected, an identifier-like field is provisional and a warning is shown. This is schema-based drafting, not semantic business-rule inference: review the key, types, requiredness, quality rules and privacy policy before execution. If multiple files were uploaded, the draft is based on the first successful upload; review mappings for the other selected sources. User-edited/generated specifications are not silently overwritten.

| Format | Extraction behavior |
|---|---|
| CSV | Header-based, comma-separated by default; duplicate headers/extra cells fail. Missing cells become null for subsequent DQ. |
| TSV | Tab-separated headers and records. |
| JSON | Object, array, or `records` / `data` array envelope; nested object paths are flattened for mapping. |
| JSONL / NDJSON | One JSON object per nonempty line. |
| TXT | One nonempty line becomes `record_id` + `text`; select pipe, comma, tab or semicolon for tabular TXT. |
| Parquet / PQ | Real Arrow Parquet decoding; dates/times become ISO strings and decimals remain strings until an explicit cast. Binary fields require an adapter. Metadata row/column/expanded-byte limits are checked before batch decoding. |

Text encoding is UTF-8. Parquet metadata limits are safeguards, not a hardened parser isolation boundary; untrusted large files need worker memory limits and ingress scanning.

## Append, joins and source lineage

Selecting several sources in Pipeline studio maps each source independently, then appends normalized rows to one batch. This supports different headers and nested paths through aliases, overrides or reviewed semantic mappings. Business-key deduplication applies across the batch.

The **Create merged source** action also supports:

- **Append:** requires identical top-level columns and retains all rows. It does not silently deduplicate.
- **Inner join:** exactly two datasets, retaining matched left rows.
- **Left join:** exactly two datasets, preserving unmatched left rows with an empty right object. Optional target defaults can supply an explicit Unknown dimension key.

The first selected dataset is the left side. Right-hand keys must be unique; many-to-many fan-out is rejected. Null join keys do not match. String `001`, integer `1` and string `1` are deliberately different identifiers. Joined fields use `left.*` and `right.*` paths; use aliases or reviewed mappings. A merged source records parent dataset IDs and checksums. Do not select the merged dataset and its parents in the same execution unless duplication is intentional. PII columns are decrypted only in server memory for joins and re-encrypted under their new column context.

## Bronze and extraction control

Non-PII sources retain exact original bytes, SHA-256, parsed records and an immutable dataset ID. Metadata includes `_ingested_at`, `_source_system`, `_batch_id`, `_file_name` and `load_date`. These are envelope metadata, not injected into every business record. The local store is SQLite; `load_date` is a logical partition label, not an ADLS directory partition.

For PII sources, selected cells are encrypted and the Bronze object is a protected JSON replay snapshot. **Original unprotected PII file bytes are not retained.** Original source checksum and protected-object checksum are distinct. See PII_HANDLING.md.

Extraction control records capture start/end, duration, row count, checksum, source, dataset and status. Failed parses/contract/privacy checks retain metadata, not unparsed raw bytes, because those bytes may contain unclassified PII. Correct the file and upload again. Interrupted extracts are marked at startup. Watermark fields are null because this file/API path reads full snapshots.

## Source profiling and contracts

Profiles compute exact statistics over the bounded extract: flattened schema/order, row counts, missing/null counts and percentages, cardinality, observed types and numeric ranges. Encrypted fields withhold value statistics. Snapshot trends compare same-named uploads and clearly identify the result as a snapshot difference, not upstream growth measurement. Mutable versus append-only is an owner declaration, not guessed from one extract.

Save a versioned source contract using the uploaded filename as `source_name`. It can declare owner, update pattern, classification, expected flattened schema and order, expected row count, a control column/total/tolerance, and PII paths/key reference. Drift modes:

- `fail`: reject new/dropped/type-changed/reordered columns.
- `alert`: accept the extract and record drift in metadata for review.
- `evolve`: accept the observed source schema for this extract; it does not rewrite or weaken the target contract.

A rename appears as a dropped plus added path; it is not guessed automatically. Contract revisions are immutable. Full-snapshot ingestion is active; watermark, CDC and stream declarations cannot be executed by this file connector.

## Silver transformations and quality

Mapped source columns are projected into the explicit target contract. Supported column operations include trim/case, whitespace/control-character cleanup, explicit US-format currency parsing, numbers, unit-to-metres conversion, strict ISO dates, timezone-required UTC timestamps, boolean-to-0/1, round-to-two-decimals, curated reference value maps, defaults, required/allowed/range rules and email/UUID/date format checks. Generic SHA-256 is available for non-security uses; use keyed tokens for PII.

Ordered `derived_columns` support add/subtract/multiply/divide, concatenation, coalesce, year and month. They reference mapped or earlier derived fields. Division by zero is quarantined. Derivations do not evaluate Python, SQL, or arbitrary expressions. Numeric arithmetic uses finite Python floats; regulated fixed-point accounting needs a decimal engine/contract extension.

`row_filters` operate on typed values with explicit comparisons. Filtered rows are persisted separately and are not DQ failures. Default deduplication quarantines repeated business keys. `latest` chooses the greatest declared order field, records superseded rows as exclusions, and keeps the first input row on ties. Normalize timestamp order fields to UTC first. It performs exact-key deduplication, not fuzzy matching.

DQ reject ratio is `rejected / (input - deliberately filtered or superseded)`. A batch needs at least one accepted row. Critical batch checks block publication; warning checks are reported but continue. Supported checks: accepted-row bounds, sum bounds, freshness in minutes, accepted-row change percent versus the previous successful target run, and reference-key existence in a previously published target. Missing volume baseline is explicit, not invented. Warning/drift statuses are recorded; separate alert-channel integration is still required.

## Transactional load strategies

| Strategy | Local behavior |
|---|---|
| Upsert / Type 1 | Insert/update by business key, preserving unmatched existing keys. |
| Append | Reject any collision with an existing target key; never overwrite. |
| Full refresh | Replace the complete target only after validation, inside the transaction. |
| Partition overwrite | Require explicit partition column/values; replace only those partitions. Keys colliding outside the overwrite scope block the load. |
| SCD Type 2 | Hash tracked attributes, expire changed current versions and insert a new version with SHA-256 surrogate key, effective bounds and current flag. Untracked values update the current history record without adding a version. |

SCD history uses load time, not event time. Missing keys do not imply deletion. A target cannot silently switch between ordinary and SCD2 loads; migrate explicitly. Type 3, late-fact as-of joins and inferred dimension members are not implemented. Randomized encrypted values cannot be business keys, grouping/order fields or SCD tracked attributes; use HMAC companion columns for stable identity/change detection.

The transaction writes Silver candidates and quarantine, applies the approved detail load, rebuilds Gold, reconciles published records, and records the run. Critical gate failures preserve diagnostics but leave detail and Gold unchanged. Unexpected publication/Gold errors roll back the entire publication transaction. Same idempotency key + identical spec/source fingerprints returns the recorded run; changed input with the same key conflicts.

## Gold and replay

Optional Gold groups by declared dimensions and calculates count/sum/average/min/max over the **full current detail target**, avoiding double counting repeated upserts. Numeric measures require numeric fields. Count without a column counts rows; count with a column counts nonnull values. Other all-null measures return null. The local Gold rebuild limit is 100,000 detail rows.

Successful run Gold snapshots retain contributing detail run IDs. Failed batches do not replace current Gold. Removing Gold from a successful recipe invalidates the current Gold pointer, avoiding a stale published summary. Configuration rollback does not restore warehouse data. Reprocess retained source snapshots with a reviewed specification and a new batch key; destructive/full replacements still require deliberate load configuration.

## STTM and APIs

`pipeline.yaml` drives execution. Generated artifacts include an STTM draft and destination plan/DDL. `/api/runs/{id}/sttm` adds exact resolved per-source mappings, derived fields, privacy policies, filters, business grain, target/load and version reference. The run is the immutable execution contract; managed pipeline versions remain available for reviewed changes.

Useful APIs: `/api/datasets/{id}/profile`, `/api/datasets/{id}/bronze`, `/api/sources/contracts`, `/api/extractions`, `/api/datasets/merge`, `/api/medallion/preview`, `/api/runs/{id}/layers/silver`, `/api/runs/{id}/layers/gold`, `/api/runs/{id}/filtered`, `/api/runs/{id}/sttm`, `/api/runs/{id}/export`, `/api/medallion/gold`, `/api/dimensions/history`.

Exports require a successful gated run. Failed-run Silver candidates remain inspectable but are not exported as approved output. Cloud credentials, live SQL Server extraction, CDC, streaming, pagination/token-refresh connectors, orchestration, cloud bulk loading and warehouse integration tests remain integration work.
