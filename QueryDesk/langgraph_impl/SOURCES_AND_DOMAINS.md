# Data sources and business domains

[Row-level security](ROW_SECURITY.md) is configured separately per source-profile ID. It supports direct region/country/tenant predicates, inherited parent restrictions and trusted group/account attributes. A connected source needs its own reviewed mappings and existing PII classification; it does not inherit sample-table names. Unmapped source/table rules deny rows when enabled. SQLite is verified end to end. Registered remote engines have local rewriting/contract tests, including translated SQL-runtime checks for the added warehouse adapters; live warehouse verification remains pending.

## Supported sources

Remote connections default to verified TLS 1.2+. The operator can permit development plaintext using `WAREHOUSE_TLS12_ENABLED=false`; managed HTTPS APIs retain encryption. See [TLS_CONNECTIONS.md](TLS_CONNECTIONS.md) before configuring local proxy or non-TLS development endpoints.

SQLite remains the default. PostgreSQL and MySQL adapters provide metadata discovery, foreign keys, scoped read-only SQL validation, dialect-aware generation and bounded results. Additional adapters now implement SQL Server/Azure SQL, Oracle, Snowflake, AWS Redshift and GCP BigQuery. See [WAREHOUSE_CONNECTORS.md](WAREHOUSE_CONNECTORS.md) for drivers, AWS/GCP configuration, identities, limits and verification status. Add another adapter behind the same catalog/schema/validate/execute interface rather than embedding vendor logic in the agents.

SQLite now supports [direct protected reads](DIRECT_READS.md), with local PII handling retained where needed. `/api/config` and approval manifests report adapter execution capabilities. Follow [CONNECTOR_CONTRACT.md](CONNECTOR_CONTRACT.md) for new engines: preserve common identity, row/PII policy, approval and recovery contracts, implement dialect-specific enforcement, and register/test the protected adapter explicitly. No sample-table logic is required for a new source profile.

Source profiles are operator configuration, not browser-submitted URLs. Browser users select an existing profile and can test the connection. This keeps passwords, endpoints and server file paths out of frontend configuration and prevents arbitrary client-selected connection targets.

The main requirements and normal setup now include all registered connectors. From the repository root, the operator can install the complete server dependencies directly:

```powershell
.\langgraph_impl\.venv\Scripts\python.exe -m pip install -r langgraph_impl/requirements.txt
Copy-Item langgraph_impl/sources.example.json langgraph_impl/sources.json
```

Edit the profile names, schemas and `allowed_tables` to match your databases. Set these in `langgraph_impl/.env`, then restart the app:

```dotenv
DATA_SOURCES_FILE=sources.json
ANALYTICS_POSTGRES_URL=postgresql://readonly_user:URL_ENCODED_PASSWORD@localhost:5432/analytics
REPORTING_MYSQL_URL=mysql://readonly_user:URL_ENCODED_PASSWORD@localhost:3306/reporting
```

The examples are placeholders; no external databases or credentials are bundled. Use dedicated SELECT-only accounts and appropriate TLS. PostgreSQL TLS URL options include `sslmode`, `sslrootcert`, `sslcert`, `sslkey` and `ssl_min_protocol_version`, subject to the configured TLS enforcement policy. MySQL permits `ssl_ca`, `ssl_cert`, `ssl_key`, `ssl_verify_cert` and `ssl_verify_identity`. Arbitrary URL driver options/plugins are rejected. The connector does not grant access or configure certificates for you.

Profiles support `id`, `name`, `kind`, `connection_env`, optional `schema`, `allowed_tables`, optional `domains_file`, optional `knowledge_directory`, optional `row_limit` (1–1000) and optional `timeout_seconds` (positive, at most 60). SQLite profiles use an environment variable containing a file path. PostgreSQL defaults to schema `public`; MySQL uses the database named in its URL. Ordinary profiles expose regular tables only in that namespace. SQL Server can explicitly opt into [registered cross-database/schema mappings](SQLSERVER_CROSS_DATABASE.md) using `table_mappings` and optional reviewed `relationships`; domains and policies then use logical table IDs. Views, linked servers and unregistered objects remain unavailable. A custom `knowledge_directory` supplies source-specific catalog/examples using the existing JSON format. Otherwise external sources start with generic context and no sample-retail definitions, units or examples.

After restarting, use **Data source → Test connection**, inspect **Data explorer**, then submit a question. Source selection is also available with CLI `--source PROFILE_ID --schema` or `--source PROFILE_ID "QUESTION"`. Bundled evaluation suites remain specific to the SQLite sample fixture.

SQL is parsed for one SELECT, restricted functions, confirmed table names and schema. The PostgreSQL/MySQL adapters qualify physical tables with the configured schema and execute in a read-only transaction. App review planning performs local checks and metadata reflection; explicit trusted-operator validation may use EXPLAIN. The new engines use their documented transaction/grant boundaries. The UI shows the normalized SQL actually executed. PostgreSQL uses a statement timeout; MySQL uses its session SELECT timeout. Read-only database grants remain essential, including restrictions on privileged/custom functions. Decimal and large-integer values use the lossless tagged JSON codec; dates become ISO strings. Decimal precision, driver/server timeout coverage, metadata size and DB-specific functions need validation for your production workload. These adapters have unit/mocked transaction tests; no live PostgreSQL/MySQL server has been supplied for end-to-end certification.

The implementation follows [SQLAlchemy reflection](https://docs.sqlalchemy.org/en/20/core/reflection.html), [PostgreSQL read-only execution options](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html), and [MySQL SELECT timeout settings](https://dev.mysql.com/doc/refman/8.4/en/optimizer-hints.html).

Remote result reads use streaming cursors and fetch at most the row limit plus one. Queries without a top-level limit receive that cap; larger literal limits are capped too, while smaller limits are preserved. Partial results are disclosed. Parameterized/complex limits remain subject to streaming and driver behavior; this is not a universal resource sandbox.

## Business domains are relevance hints

A domain describes a business area, its terms and useful starting tables. It does not grant or remove database access. The UI passes a domain's preferred tables and descriptions to schema selection, while keeping all source-permitted tables available. If additional tables are proposed, the table-confirmation reason identifies them. The final confirmed tables and source permissions still strictly constrain SQL validation and execution.

Example: select **Stock planning** and ask about sales. The model can propose `orders` if it is permitted, even when it is outside the domain's preferred list. Users still review those tables before SQL generation.

The older CLI `--workspace` option retains its explicit metadata restriction for backward compatibility; UI business domains use the new soft-hint mode.

## Three ways to define domains

1. **Administrator/business owner:** open Business domains for the selected source, create/edit its name, description, business terms and tables, then save. Domain editing is admin-only because membership grants depend on these definitions. Edits persist in `outputs/querydesk.sqlite3`; revision checks prevent overwriting another editor's save.
2. **LLM draft:** choose Suggest domains with AI. The model receives permitted names, columns, types and relationships, not table rows or credentials. Drafts can overlap; they do not automatically change saved domains. Choose Review, inspect/edit the fields, then Save domain. Oversized metadata is rejected rather than silently truncated. Suggestions may require correction and are subject to provider quotas.
3. **Developer config:** copy `domains.example.json`, adapt table names and definitions, and point the source at it. Config files are read on startup. They never override source table permissions.

For the sample source:

```dotenv
DOMAINS_FILE=domains.json
```

For an external profile, add:

```json
{
  "id": "postgres_analytics",
  "name": "PostgreSQL analytics",
  "kind": "postgresql",
  "connection_env": "ANALYTICS_POSTGRES_URL",
  "schema": "public",
  "allowed_tables": ["customers", "orders", "order_items"],
  "domains_file": "analytics-domains.json"
}
```

The domains file is a JSON list:

```json
[
  {
    "id": "customer_sales",
    "name": "Customer sales",
    "description": "Customer purchasing patterns and order quantities. Additional payment tables may be useful for cash questions.",
    "keywords": ["customers", "buying", "sales"],
    "tables": ["customers", "orders", "order_items"]
  }
]
```

Built-in/source-file domains form the initial catalog. Saved UI definitions override matching IDs; new IDs add domains. File domain IDs and tables are validated. Existing queries keep their workflow context. Domain definitions are not verified financial metric formulas; use source-specific business definitions for those.

## API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/sources` | Safe configured source summaries |
| `POST /api/sources/{id}/test` | Read-only connection/metadata check |
| `GET /api/config?source={id}` | Source-specific domain choices/configuration |
| `GET /api/schema?source={id}` | Source-specific permitted metadata |
| `GET /api/metrics?source={id}` | Source-specific definitions |
| `GET /api/sources/{id}/domains` | Effective saved/configured domains and permitted table list |
| `POST /api/sources/{id}/domains` | Create/update a reviewed domain with its revision |
| `POST /api/sources/{id}/domains/suggest` | Generate metadata-based drafts without saving them |

`POST /api/queries` accepts a source ID and uses its existing `workspace` field as the UI domain hint. It never accepts connection strings, credentials, SQL or arbitrary database paths. Authentication and inherited domain/PII grants are enforced; hints can expand only within the user's assigned domain union. See `SECURITY.md`. Scripts still run locally rather than deploying a public service.


## Business-owned domain knowledge

A domain/table configuration is a relevance/access foundation; business definitions are maintained separately in **Domain knowledge**. Administrators assign maintenance roles without adding data grants. See [the role-specific authoring guide](DOMAIN_KNOWLEDGE.md#quick-start-by-role) and [current architecture](ARCHITECTURE.md) for the publication and generation paths.
