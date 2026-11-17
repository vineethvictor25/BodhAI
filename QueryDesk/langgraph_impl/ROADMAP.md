# Application roadmap

## Planned enhancement: durable job coordination

Status: **backlog — implementation deferred**. Keep the supported single-API-process deployment until this enhancement is implemented and verified. No implementation date is assigned.

Scope: PostgreSQL shared application state and queue, worker leases/heartbeats/fencing, atomic approval consumption, shared cancellation/quotas, and consistent security/configuration across instances. Uncertain warehouse executions must not be automatically replayed.

Delivery order: shared storage contracts → PostgreSQL control store → multiple workers behind one API → distributed review/execution/cancellation checks → multiple API instances. The [feasibility assessment](DURABLE_COORDINATION_FEASIBILITY.md) records the design, prerequisites and release gates for future implementation.

## Delivered enhancements

Implemented: [business-owned domain knowledge](DOMAIN_KNOWLEDGE.md), with separate maintenance assignments, validated JSON import, optional metadata-only AI drafts, immutable revisions, reviewer publication, rollback/retirement, permission-filtered generation context and atomic approval invalidation. Metadata validation is not automated business-result verification; graph/vector storage and feedback learning remain deferred.

Implemented: [synthetic analytics lab](ANALYTICS_LAB.md) with 20 tables, 399,408 rows, a combined local source, separate related SQLite databases, SQL Server seed/mapping artifacts and 12 verified reference queries. Native SQL Server deployment remains an operator step; predictive fixtures do not add model training or federation.

Implemented: [optional result visualizations](VISUALIZATIONS.md), with a Visualization Agent selecting chart/axes and Plotly, Matplotlib or Seaborn from installed allowed adapters. Interactive and static charts remain in the browser. Unsuitable results get editable query-improvement suggestions with fresh table/SQL approvals. Other chart libraries require future adapters.

Implemented: [admin operational metrics, durable submission idempotency and isolated load/failure exercises](OPERATIONS_AND_RELIABILITY.md). Metrics are process-local, and distributed work coordination plus live-provider/warehouse capacity verification remain future work.
Per-agent model environment overrides, bounded configurable worker/queue/user limits, Stop/Edit controls and revalidated SQL editing are available. See [query controls](QUERY_CONTROLS.md) for supported cancellation behavior and remaining distributed scaling limits.

Implemented: database/schema/table grouping in mapped-source table confirmation, with suggested selections, individual and group checkboxes, mixed selection states and permitted-only metadata. Review recovery refreshes table locations; SQL approval and existing source/domain/PII/row restrictions remain in force. See [UI.md](UI.md) and [SQLSERVER_CROSS_DATABASE.md](SQLSERVER_CROSS_DATABASE.md).

Implemented: [same-instance SQL Server cross-database joins](SQLSERVER_CROSS_DATABASE.md) using explicit logical-to-physical table mappings, reviewed relationship metadata, source/domain/PII/row protections and approval of expanded SQL. Local tests and authenticated API checks pass; live SQL Server/runtime-role verification remains pending. Cross-server and cross-engine federation remain future work.

Implemented: native query-result tables with accessible headers, horizontal scrolling, exact scalar formatting and separate notices for empty, limited or unretained results. The [dataset guide](DATASET.md#example-questions) includes an eight-table question and reference preview. This adds tabular query output; dashboards, chart authoring and exports remain outside the current UI.

[Configurable warehouse TLS](TLS_CONNECTIONS.md) now defaults to verified TLS 1.2+, permits explicit development plaintext for supported SQL engines and binds the selected transport policy to approval. Snowflake/BigQuery retain HTTPS. Local handshake and configuration checks are implemented; live warehouse certificate/IAM/runtime verification remains pending.

[Additional warehouse adapters](WAREHOUSE_CONNECTORS.md) now include SQL Server/Azure SQL, Oracle, Snowflake, AWS Redshift and GCP BigQuery, with per-source limits, BigQuery ADC/cost controls and example AWS/GCP configuration. Local contract tests are implemented; live runtime/IAM/TLS/performance verification remains pending. Databricks, Athena, Synapse and AWS IAM temporary database authentication remain future work.

[Bounded encrypted PII handling](BOUNDED_PII.md) now enforces configurable row, input/plaintext byte, envelope/cell, decryption-count and preparation-time limits. Limits participate in SQL approval; failures return no partial result and stop automatic repair. SQLite local temporary work stays in memory. Streaming and other database encryption adapters remain future work.

The eight-point hardening plan is in [HARDENING_PLAN.md](HARDENING_PLAN.md). Implemented increments cover [history/exact values/provider policy](PHASE_A.md), [SQL approval](SQL_APPROVAL.md), [non-blocking reviews](NON_BLOCKING_REVIEWS.md), [review recovery](RESTART_RECOVERY.md), [row security](ROW_SECURITY.md), [direct protected reads](DIRECT_READS.md) and [selective local PII copying](COPY_REDUCTION.md). The [connector contract](CONNECTOR_CONTRACT.md) keeps governance common across engines. Streaming PII processing, broader safe predicate strategies, stronger semantic evidence, measured capacity, live warehouse verification, in-flight recovery, distributed dispatch and report/audit/backup retention remain open. Running/uncertain executions are not replayed.

Security implementation includes first-run admin setup, one-domain onboarding, inherited domain/PII grants, ownership/revocation, local AES-GCM and SQLite column migration. Configurable row rules now add region/country/tenant predicates and parent-policy inheritance. The feature is disabled until mapped/enabled by the operator; unsupported SQL fragments/operators are rejected. Standard groups remain Business Users, PII Readers and PII Restricted. See `SECURITY.md` and `ROW_SECURITY.md`.

The UI and the three enhancements below are implemented. See `ENHANCEMENTS.md` for operation, limits and verification. No implementation dates are assigned for future work.

## Current: application UI

Provide a browser-based question feed showing answers, data-based explanations, selected tables, prepared/executed SQL, workflow progress and actionable errors. FastAPI serves the backend; Node.js/React serves the frontend. Provider credentials remain in local server configuration. No user downloads or exports are included. See `UI.md` for setup and verification status.

## Implemented after the UI

1. **Evaluation reliability and persistence**
   - Bounded recovery from provider rate limits, respecting retry guidance.
   - Save each completed run and support resuming interrupted evaluations.
   - Record the actual provider/model configuration and summarize correctness, failures, latency and consistency.
   - Begin with a small repeated live evaluation before expanding the batch.

2. **Independent question bank**
   - Separate held-out questions from approved SQL examples and generation context.
   - Add unfamiliar phrasing, difficult joins, NULLs, partial payments/refunds, time boundaries, ambiguity and unsupported requests.
   - Review expected results, comparison rules, domain tags and difficulty labels.

3. **Table confirmation and feedback**
   - Show proposed workspaces/tables in the UI and allow confirmation or edits before SQL generation.
   - Enforce existing permissions and refresh schema/pruning after edits.
   - Associate feedback with the question and query; review it before promoting it to approved examples or evaluation cases.

The existing definitions, workspace routing, SQL-example retrieval, column pruning and evaluation runner remain the foundation for this work.

The question bank now includes 30 development and 37 held-out cases. Evaluation reports separate result correctness from a strict normalized SQL/reference match diagnostic. A Playwright test exercises the production UI through a temporary local API and sample database with a deterministic workflow fixture. Completed UI snapshots and feedback persist in a separate local SQLite file; evaluation reports checkpoint atomically and support guarded resume. SQL execution approvals themselves are not persisted.

Future work: independent business review of held-out expectations and optional hybrid keyword/vector retrieval for schema descriptions, business definitions and reviewed examples. Evaluation answers and unreviewed feedback must remain outside generation retrieval.

The current UI deployment is single-API-process only. Before multi-instance operation, implement a shared durable job queue, atomic work/review claims, distributed single-use approval consumption, cross-process cancellation and bounded retry behavior. Then test separate users across workers, duplicate submissions, restart/failover during waiting and executing phases, queue saturation, rate-limit sharing, and stale/uncertain execution recovery. Add privacy-safe queue/provider/database telemetry and operational runbooks for graceful shutdown, backups, restore and key rotation. A shared application SQLite file alone does not provide these coordination guarantees.

Also implemented: operator-configured profiles for all eight registered engine kinds, source selection/testing, and persisted business domains created manually, supplied by developer JSON or proposed by AI for review. UI domains guide relevance rather than strictly restricting tables. Additional source-permitted tables can be proposed for confirmation. See `SOURCES_AND_DOMAINS.md`; live external-server verification is still pending.
