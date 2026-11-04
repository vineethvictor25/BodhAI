# Cloud and warehouse selection

Pipeline studio offers cloud/warehouse dropdowns and destination-dependent loading fields. Supported **planning/export** choices are:

| Cloud | Destinations |
|---|---|
| Local | SQLite, with working transactional local execution |
| Azure | Azure SQL / SQL Server, Synapse dedicated SQL pool, Snowflake, Databricks SQL |
| AWS | Redshift, Snowflake, Databricks |
| Google Cloud | BigQuery, Snowflake, Databricks |

Selecting a cloud changes validation, required configuration, generated DDL, staging and bulk-load guidance. It does not establish a connection. Cloud publication is explicitly blocked by the current runtime; local transformation previews remain available. The UI and API report `export_plan_only`, missing settings and connector availability. Configuration completeness means fields are present, not that credentials, networks or a warehouse were verified.

## Configuration that varies by destination

- SQL Server/Azure SQL: endpoint, database, staging schema, managed identity or Key Vault reference; ODBC/bcp staging design.
- Synapse: endpoint, dedicated SQL pool, ADLS staging URI, managed identity; COPY loading design.
- Snowflake: account, database, compute warehouse, role, approved external stage and external identity reference; COPY staging design.
- BigQuery: project, location, GCS prefix and workload identity; load-job design.
- Redshift: endpoint, database, S3 prefix, IAM COPY role and identity reference.
- Databricks: workspace HTTPS URL, SQL warehouse ID, Unity Catalog external location and OAuth/identity reference.

Password, access-token and connection-string settings are rejected. Use references such as `managed-identity://system`, `workload-identity://loader`, or `keyvault://vault/secret-name`. Signed staging URLs are rejected. These destination identity references are validated configuration, not yet resolved by a warehouse adapter. The separate PII key provider can actually resolve Azure Key Vault secrets; see PII_HANDLING.md.

Load options are upsert, append, atomic full replacement, explicit partition overwrite and SCD2. Their semantics drive local execution and exported cloud plan steps. These are not fabricated vendor MERGE scripts: a cloud adapter still needs target-specific transactions, bulk import, permissions, staging cleanup and integration tests. DDL is a reference schema with native type/identifier conventions; provision namespaces, verify types, physical layout and warehouse constraints before using it.

## Persist and export

Select cloud → warehouse → load strategy, enter its fields, then **Apply configuration to specification**. Invalid combinations/settings fail validation. **Review destination load plan** shows the effective configuration, missing fields, staging method, load semantics and remaining integration work. Export artifacts to obtain `pipeline.yaml`, `sttm.json`, `destination-plan.json`, `destination-ddl.sql` and the local runner. A cloud-configured runner fails explicitly rather than writing locally.

## Azure / SQL Server target design

The selected first integration target is SQL Server plus Azure. Recommended design: read-only SQL account or Entra identity on a read replica; private network route; Azure Data Factory for extraction orchestration; ADLS Gen2 load-date partitions for Bronze; a governed Spark/SQL Silver worker; staged Azure SQL/Synapse or the selected warehouse load; Gold marts after dimensions/facts; Key Vault-backed identities; centralized telemetry and immutable audit. This design is not deployed by this release.

Before implementing a live connector, agree source tables and business keys, maintained watermark versus log CDC, hard-delete semantics, volume/SLA, target warehouse, network path, identity/grants, and expected backfill windows. A managed identity authenticates; database grants and network controls still establish least privilege. Do not use a read-only intent setting as a substitute for grants.

Current API: `GET /api/destinations/catalog`, `POST /api/destinations/plan` (RunRequest envelope), and `POST /api/artifacts` (PipelineSpec). No endpoint provisions cloud resources or performs remote writes.
