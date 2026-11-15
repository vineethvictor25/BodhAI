# On-premise and cloud database connectors

The source registry now supports eight engine kinds. Browser users select configured sources; only the application server needs drivers. Credentials and connection targets remain operator configuration, outside the browser and model context. Each source uses its own allowed tables, business domains, PII classification, row rules and approved provider profile.

| Engine kind | Deployment examples | Adapter and server prerequisite |
| --- | --- | --- |
| `sqlite` | Local database | Existing built-in adapter |
| `postgresql` | On-premise, AWS RDS/Aurora, GCP Cloud SQL | Existing SQLAlchemy/psycopg adapter |
| `mysql` | On-premise, AWS RDS/Aurora, GCP Cloud SQL | Existing SQLAlchemy/PyMySQL adapter |
| `mssql` | SQL Server, Azure SQL Database/Managed Instance, RDS SQL Server | SQLAlchemy/python-tds and PyOpenSSL; TLS 1.2 enforced by the driver |
| `oracle` | Oracle 12c+ on-premise/cloud | SQLAlchemy/python-oracledb Thin mode; no Instant Client required |
| `snowflake` | Snowflake on AWS, GCP or Azure | Snowflake SQLAlchemy and Python connector |
| `redshift` | AWS provisioned clusters or Redshift Serverless | SQLAlchemy Redshift 1.x dialect and psycopg2 |
| `bigquery` | Google Cloud BigQuery | Native Google BigQuery client, Application Default Credentials (ADC) |

## Install and configure

From the repository root, install the complete server requirements in the application's virtual environment:

```powershell
.\langgraph_impl\.venv\Scripts\python.exe -m pip install -r langgraph_impl/requirements.txt
```

The main `requirements.txt` now includes every server connector and its TLS libraries; normal `setup.ps1` installation uses the complete `requirements-lock.txt`. Locks record Windows/Python 3.11 versions used for local verification. Per-engine `requirements-mssql.txt`, `requirements-oracle.txt`, `requirements-snowflake.txt`, `requirements-redshift.txt` and `requirements-bigquery.txt` remain available for individual connector installation. SQL Server uses a Python TDS driver and needs no ODBC installation.

Merge needed entries from `sources.warehouses.example.json` into the single JSON list in your existing `sources.json`. For a fresh deployment, copy the example to `sources.json`. Existing PostgreSQL/MySQL examples remain in `sources.example.json`. Adapt schemas and table names; these examples do not provision databases or create sample warehouse tables.

`sources.cloud-managed.example.json` additionally provides AWS RDS/Aurora PostgreSQL and GCP Cloud SQL PostgreSQL profiles matching the environment template. Merge selected entries into the same list; the app reads one configured profile file.

Merge only required settings from `.env.warehouses.example` into `.env`, including:

```dotenv
DATA_SOURCES_FILE=sources.json
BIGQUERY_URL=bigquery://your-gcp-project/analytics?location=US&maximum_bytes_billed=1000000000
REDSHIFT_URL=redshift://reader:URL_ENCODED_PASSWORD@workgroup.account.region.redshift-serverless.amazonaws.com:5439/reporting?sslmode=verify-full
```

Passwords must be URL encoded. Drivers are selected by registered engine kind; arbitrary URL plugins, authentication modules, file-loading options and session SQL are rejected. Do not put credentials in source profiles. Existing configured profiles and `.env` values are not replaced by this increment. Restart the backend after changing configuration, use **Data source → Test connection**, and inspect metadata before enabling real questions.

Profiles accept optional `row_limit` (1–1000, default 100) and `timeout_seconds` (positive, at most 60, default 3). Cloud examples use 30 seconds to allow warehouse startup/queueing. These settings participate in final SQL approval. A result-row cap does not bound scanned data, driver prefetch or total process memory.

`WAREHOUSE_TLS12_ENABLED=true` defaults to verified TLS 1.2+ for remote SQL connections. Set it to `false` to permit plaintext development SQL connections; explicit encrypted URL settings remain honored, and Snowflake/BigQuery always use HTTPS. [TLS_CONNECTIONS.md](TLS_CONNECTIONS.md) explains each engine, trusted CA options and approval-policy changes.

## Google Cloud

For BigQuery, the URL host is the billing/query project and the path is the only permitted dataset. `schema` must match that dataset. Explicit location is required and must match the dataset. Cross-project/cross-dataset queries, external tables, views, table functions, scripts, exports and nested/repeated columns are currently rejected. Regular tables with flat scalar columns are supported. Relationships use declared table constraints where available; source-specific business definitions still need review.

Prefer ADC through an attached service account/workload identity. Grant job creation on the query project (for example BigQuery Job User) and read/metadata access only on approved tables/datasets (for example BigQuery Data Viewer). Do not use an owner/editor identity. For a local operator, an optional absolute `GOOGLE_APPLICATION_CREDENTIALS` path can be configured in `.env`; this fallback expects a service-account JSON file stored outside the repository. The app does not create identities or change IAM.

The adapter performs metadata API calls during discovery/planning and submits query jobs only after SQL approval. Named parameters support both `:name` and BigQuery `@name`, with typed SDK parameters. `maximum_bytes_billed` defaults to 1,000,000,000 bytes and must be positive and at most 1 TB; it and location appear in the approved manifest/UI. This is a billed-byte cap, not a currency budget. LIMIT alone does not cap scan costs. BigQuery's job timeout is best effort; on failure/timeout the app attempts cancellation and disables automatic job retries. Cancellation can race completion and cannot guarantee zero charges. Native temporary query-result storage is managed by BigQuery, separately from app history retention. See [QueryJobConfig](https://docs.cloud.google.com/python/docs/reference/bigquery/latest/google.cloud.bigquery.job.QueryJobConfig).

GCP Cloud SQL PostgreSQL/MySQL use the existing engine adapters. The example PostgreSQL address assumes an independently configured Cloud SQL Auth Proxy on loopback; the app does not start that proxy. Direct private-IP access instead requires appropriate network routing and TLS configuration.

## AWS

Redshift accepts an endpoint reachable from the app server, database, explicit schema, dedicated database username/password and certificate-verified TLS. With enforcement enabled, `sslmode=verify-full`, optional `sslrootcert` and a TLS 1.2-or-newer `ssl_min_protocol_version` are accepted. Development mode can explicitly select `sslmode=disable`. Supply the correct CA trust configuration. This works with provisioned or Serverless SQL endpoints; IAM temporary credential generation, the Redshift Data API and Secrets Manager integration are not implemented. Configure network/security groups/private routing externally.

Each Redshift session requests a read-only transaction and sets `statement_timeout`; dedicated SELECT-only database grants remain essential. Reflection uses the Redshift dialect rather than treating the engine as PostgreSQL. Timeout covers server queue/planning/execution as described in [AWS statement_timeout](https://docs.aws.amazon.com/redshift/latest/dg/r_statement_timeout.html). Native application row filters still require source-specific mappings.

AWS RDS/Aurora PostgreSQL/MySQL reuse existing adapters and TLS options. RDS SQL Server uses `mssql`; Oracle deployments use `oracle` if they meet the Thin-mode service/transport requirements. S3 buckets are not SQL warehouse connections; Athena and data-lake federation remain separate future adapters.

## SQL Server, Oracle and Snowflake

SQL Server can opt into [same-instance cross-database mappings](SQLSERVER_CROSS_DATABASE.md) through `table_mappings` and reviewed `relationships`. Use `sources.sqlserver-multi.example.json` as a starter. One login must have access to every registered object, while app users still need the required domain/table and PII permissions. Unconfigured SQL Server profiles and other engines retain their current namespace restrictions.

SQL Server uses the Python TDS driver with TLS 1.2, a trusted CA file, hostname checks and full-channel encryption when enforcement is enabled. A read-only connection intent supplements a dedicated SELECT-only login; it does not grant or enforce privileges by itself. Login/query timeouts are passed to the driver, with rollback and no app connection pooling. Development transport behavior is documented in [TLS_CONNECTIONS.md](TLS_CONNECTIONS.md). See [SQLAlchemy SQL Server](https://docs.sqlalchemy.org/en/20/dialects/mssql.html).

Oracle requires an explicit uppercase schema and `service_name` URL option; SID/DSN aliases and arbitrary wallet/plugin options are rejected. Transport defaults to verified `tcps` with a TLS 1.2 minimum; `protocol=tcp` is available only when the operator sets `WAREHOUSE_TLS12_ENABLED=false`. This initial connector does not implement Autonomous Database wallet/mTLS configuration. It requests a read-only transaction and sets a per-round-trip `call_timeout`, not a universal wall-clock query deadline. NUMBER values use decimal conversion before floating-point loss can occur. See [python-oracledb connections](https://python-oracledb.readthedocs.io/en/latest/api_manual/connection.html).

Snowflake requires `database/schema` in the URL, matching the explicit uppercase profile schema, plus warehouse and a dedicated SELECT-only role. Administrative roles are rejected. The initial authentication path uses the URL username/password; key-pair, browser SSO, PAT/OAuth and account provisioning are not exposed. Statement/queue timeouts are applied as session parameters at connection creation. There is no read-only transaction mode in this adapter; restricted grants are the write boundary. See [Snowflake SQLAlchemy connection settings](https://docs.snowflake.com/en/developer-guide/sqlalchemy/sqlalchemy-connect).

Oracle and Snowflake profiles/policies use native names (`ORDERS`, `CUSTOMER_ID` for ordinary objects). Reflected ordinary uppercase names are restored from SQLAlchemy's lowercase convention; quoted mixed/lowercase object names retain their case. Input unquoted identifiers follow uppercase engine semantics. Use uppercase ordinary schema names; quoted lowercase schemas are outside this initial configuration surface.

## Shared access controls and limitations

All registered engines preserve source/domain/table scope, server-authenticated subjects, classified-column removal, configured source row predicates at each base-table reference, fresh final SQL approval and bounded result serialization. Metadata planning does not execute EXPLAIN against remote data. Query functions remain restricted; unsupported vendor syntax is rejected. SQL generation receives the selected dialect. Decimal results retain exact values through the existing tagged JSON codec.

Add each profile ID to PII/row/provider policy configuration before granting member access. Unmapped PII sources are blocked for members without PII permission; enabled row rules deny unmapped tables. Provider endpoint/model/source approval remains required. Shared service credentials do not automatically map app users to database-native users/RLS identities. Validate grants and native policies independently.

Remote adapters declare no local PII decryption capability. The existing bounded local AES-GCM path remains SQLite-specific; adding an engine does not enable local encryption migration or decrypt existing remote ciphertext. See [BOUNDED_PII.md](BOUNDED_PII.md) and [CONNECTOR_CONTRACT.md](CONNECTOR_CONTRACT.md).

Local verification covers real package imports/configuration objects, mocked lifecycle/metadata/jobs, dialect parsing, source-scope rejection, exact decimals, cost fingerprints and protected rewrites evaluated on a SQLite runtime after dialect translation. This is not a live database test. No customer cloud credentials or warehouse endpoints were provided; live connections, IAM/runtime roles, TLS certificates, cancellation and workload performance must be checked against each deployment. Databricks, Athena, Synapse and other engines remain unregistered and fail closed.

Recorded verification: 193 backend/API tests (including 29 focused connector/transport checks) and 29 frontend tests, a passing production frontend build and a clean dependency check. Existing sample sources, encryption settings, provider approvals and real source profiles were not changed.
