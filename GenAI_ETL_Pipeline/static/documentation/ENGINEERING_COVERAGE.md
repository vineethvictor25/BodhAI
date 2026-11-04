# Engineering checklist: implemented versus integration work

Release 1.5 uses the requested 49-point lifecycle as its design baseline. **Local** means working code tested with bounded files/local state. **Partial** names the implemented subset and the remaining work. **Design** means a target architecture or contract, not a running connector/service. Nothing in this matrix certifies production readiness.

| # | Requirement | Status and evidence / remaining work |
|---|---|---|
| 1 | Source profiling | Local: exact extract schema, count, null %, cardinality/types, ranges, snapshot trends; owner-declared update pattern. RDBMS/SaaS/stream live profiles need connectors. |
| 2 | Grain, SLA, consumers | Local: versioned business context with grain, owner, consumers and freshness requirement. SLA scheduling/enforcement remains external. |
| 3 | Ingestion pattern | Partial: working full file/API snapshots. Watermark, CDC and stream execution require connectors; unsupported declarations are blocked. |
| 4 | Target model and SCD | Local Medallion layout, fact/dimension intent, Type 1 and load-time SCD2. Data Vault/Type 3 not implemented. |
| 5 | STTM | Local executable YAML/JSON artifacts plus run-resolved per-source mappings and version references. |
| 6 | Credentials/network | Partial: real optional Azure Key Vault PII key resolver, external destination identity references. Warehouse identity/grants/private routes not connected. |
| 7 | Extraction choices | Partial: full files/public allowlisted JSON APIs, checksums/source controls. Watermark/CDC, API pagination/rate retry/token refresh, arrival detection/control-file watchers remain integration work. |
| 8 | Parallel partition reads | Design: read replicas, bounded partitions/fetch sizes for SQL Server adapter. No OLTP reader is deployed. |
| 9 | Raw landing + metadata | Local: exact non-PII bytes and envelope ingestion metadata/load date. PII uses column-protected replay snapshots. ADLS physical partitioning/Avro requires integration. |
| 10 | Extraction control | Local extraction table: start/end, duration, count, status, checksums. Watermark bounds remain null on full snapshots. |
| 11 | Schema/drift validation | Local source contracts and immutable revisions; fail/alert/evolve-observed policies. Target contracts never silently evolve. |
| 12 | Reconciliation | Local expected count/control total, original/protected checksums and post-load accepted-record reconciliation. Upstream DB control queries need connector support. |
| 13 | Loosely typed staging | Local parsed Bronze rows retained before typed Silver. Cloud transient staging tables remain loader integration. |
| 14 | Rejects | Local row quarantine and extraction-failure audit. Unparsed bytes are not retained because classification may be incomplete. |
| 15 | Casting/normalization | Local ISO dates, timezone-required UTC timestamps, numeric/currency parsing, boolean 0/1. Locale-specific formats require explicit adapters. |
| 16 | Null handling | Local required/default/keep-null, derived coalesce and explicit dimension sentinel defaults. No contextual value invention. |
| 17 | String cleanup | Local trim/case, whitespace/control cleanup, UTF-8 decoding. No automatic recovery of ambiguous encodings. |
| 18 | Reference standardization | Local curated value maps and bounded lookup joins. Effective-dated reference services remain integration work. |
| 19 | Deduplication | Local exact-key reject or latest-order selection with exclusions. Fuzzy/probabilistic identity resolution is deferred. |
| 20 | Validity/outliers | Local ranges, allowed sets, format checks and reference-key checks. Domain-specific statistical detectors need datasets/evaluation. |
| 21 | Projection/filters | Local target projection and typed row filtering. Source SQL pushdown/column pruning require extraction adapters. |
| 22 | Column mapping | Local names/aliases/overrides/approved semantic mappings; casts/defaults/derived concat. Split/system column operators need extension; ingestion system metadata is in the envelope. |
| 23 | Calculations | Local ordered arithmetic, concat/coalesce and year/month. Age/tenure/calendar/financial fixed-point engines require explicit extensions. |
| 24 | Enrichment | Local append, inner and left joins, strict typed keys, right-key uniqueness and Unknown defaults. Many-to-many fan-out is blocked. |
| 25 | Rollups | Local configured Gold count/sum/avg/min/max over full published detail. |
| 26 | Reshape/flatten | Partial: nested-object flattening. Array explode/pivot/unpivot operators are not implemented. |
| 27 | Business rules | Local curated status maps/filters and arithmetic. Effective-date FX/fiscal calendar services require reference-data integration. |
| 28 | Surrogate keys | Local deterministic SCD2 SHA-256 surrogate keys and explicit Unknown defaults. Fact-to-dimension key substitution remains a modeled enrichment step. |
| 29 | SCD | Local Type 1 upsert and load-time Type 2 with hash diff/effective bounds/current flag. Type 3 is deferred. |
| 30 | PII | Local column AES-GCM, masking, keyed tokens, protected source snapshots, outbound model safeguards, step-up decryption audit. Warehouse column roles/retention/purge remain governance work. |
| 31 | Late facts/dimensions | Design: event-date lookups, inferred members and correction policy. Current SCD2 uses load time and cannot claim as-of event correctness. |
| 32 | Transformed DQ | Local uniqueness, required/reference/accepted values, ranges, row bounds, volume change, freshness and sum checks. Framework migration is unnecessary until scale/integration justifies it. |
| 33 | Failure policy | Local critical blocks / warning reports, quarantine, audits. Automatic failure-alert routing is not yet wired to the optional change-email service. |
| 34 | Stage/promote | Local Silver candidates and one atomic target/Gold transaction. Cloud staging/promotion requires adapter tests. |
| 35 | Load strategies | Local full refresh, append, upsert, explicit partition overwrite and SCD2. Cloud selection exports their semantics, not remote execution. |
| 36 | Dependency order | Local reference checks require already-published dimensions; Gold follows detail within a run. Multi-pipeline DAG execution remains external. |
| 37 | Bulk loading | Local executemany; destination-specific COPY/load-job plan. Cloud bulk loaders and optimal object sizing are not implemented. |
| 38 | Transaction/idempotency | Local atomic transactions and fingerprinted idempotency keys. Config rollback is separate from data rollback. |
| 39 | Physical design | Local SQLite keys/indexes and WAL. Cloud partition/clustering/distribution/statistics need target profiling and IaC. |
| 40 | Post-load validation | Local exact accepted-key/value reconciliation before commit. Target-specific remote checks need adapters. |
| 41 | Orchestration | Design: Azure Data Factory for SQL Server/Azure first integration; queue-backed worker baseline. No Airflow/ADF/Dagster service runs here. |
| 42 | Recovery | Local atomic rollback, interrupted-run markers, replay with new key; bounded LLM availability retries. No ETL task checkpoint or distributed retry scheduler. |
| 43 | Audit/lineage | Local source/run/parent checksums, row provenance, resolved column STTM, contributing Gold runs and access audit. No OpenLineage/DataHub/Purview integration yet. |
| 44 | Monitoring | Local run/extract quality/drift/duration reports and optional change-email outbox. SLA/cost/volume alert delivery needs telemetry integration. |
| 45 | Performance | Bounded data, Parquet metadata checks/batches and full-detail Gold cap. Distributed pushdown, skew handling and incremental compute need a cloud engine. |
| 46 | Environments/CI | Local isolated regression tests and Docker packaging; CI workflow supplied. Cloud promotion/IaC/release rollback are not deployed. |
| 47 | Governance | Partial: application token, separate decrypt authorization, PII protection and audit. OIDC/RBAC, encrypted platform storage/TLS, retention/legal holds and deletion need operational implementation. |
| 48 | Documentation/catalog | Local guides, STTM, profiles, business ownership and runbook. Enterprise catalog synchronization is future work. |
| 49 | Backfill/replay | Local retained source snapshots + reviewed recipe + new key. PII replay requires referenced keys. Scheduling partitions/backfills on Azure requires orchestration. |

## First Azure / SQL Server delivery sequence

1. Agree grain/keys, read replica, source table allowlist, load volumes, hard-delete policy and freshness requirement.
2. Implement one read-only, identity-backed SQL Server extraction connector and validate full reads before adding a watermark with overlap/reconciliation.
3. Deploy ADLS landing, Key Vault, private paths, source controls and durable orchestration. Add SQL CDC only with retained LSN/restart/delete semantics and actual integration tests.
4. Implement one chosen warehouse staging/bulk loader and atomic promotion adapter, then validate representative SCD/history/backfill/failure cases.
5. Add named-user authorization, observability, alert delivery, restore tests, retention/deletion and controlled dev/test/prod release.

A multi-cloud configuration suite is useful now; multiple production connectors should follow proven demand rather than duplicating untested adapters across every provider at once.
