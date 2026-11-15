# Same-instance SQL Server cross-database joins

One configured SQL Server source can now expose approved tables from multiple databases/schemas on the same instance. It uses one connection and the login's existing permissions. This is implemented for SQL Server and compatible Azure SQL Managed Instance deployments; Azure SQL Database does not gain native cross-database joins through this feature. Separate server instances, linked servers and cross-engine federation remain unsupported.

## Configure a source

Merge the entry from `sources.sqlserver-multi.example.json` into the JSON list in your existing `sources.json`. Set the connection in `.env`:

```dotenv
DATA_SOURCES_FILE=sources.json
SQLSERVER_CROSSDB_URL=mssql://reader:URL_ENCODED_PASSWORD@sql.example.test:1433/DatabaseA
WAREHOUSE_TLS12_ENABLED=true
```

The login must be able to connect to the initial database, discover metadata and SELECT the registered tables in both databases. Provision those database users/grants outside the app; the app does not grant access. Configure trusted TLS certificates as described in [TLS_CONNECTIONS.md](TLS_CONNECTIONS.md). The existing TLS/development switch, result limits and driver timeouts continue to apply.

The important profile fields are:

```json
{
  "id": "sqlserver_sales_crm",
  "name": "Sales and CRM on one SQL Server instance",
  "kind": "mssql",
  "connection_env": "SQLSERVER_CROSSDB_URL",
  "schema": "dbo",
  "allowed_tables": ["crm_customers", "sales_orders"],
  "table_mappings": {
    "crm_customers": {"database": "DatabaseA", "schema": "dbo", "table": "Customers"},
    "sales_orders": {"database": "DatabaseB", "schema": "dbo", "table": "Orders"}
  },
  "relationships": [
    {"from_table": "sales_orders", "to_table": "crm_customers", "columns": {"CustomerID": "CustomerID"}}
  ],
  "knowledge_directory": "knowledge_sqlserver_multi_example",
  "row_limit": 100,
  "timeout_seconds": 15
}
```

`allowed_tables` contains stable logical IDs. `table_mappings` must have exactly the same keys; no undeclared object is discoverable/queryable simply because the database login can read it. Physical database/schema/table names must be simple ASCII identifiers, up to 128 characters each. Logical IDs are lowercase simple identifiers up to 63 characters. Matching is exact-case; mappings to the same physical identity ignoring case are rejected. Four-part server names, arbitrary SQL fragments and duplicate physical mappings are rejected.

The profile's `schema` is the logical qualifier used internally; each mapping supplies its actual physical schema. Existing SQL Server profiles without `table_mappings` retain their previous single-database/schema scope. Other engine kinds reject this configuration.

The supplied knowledge directory defines separate CRM and Sales domains without inheriting sample-retail metrics. Adapt those definitions to the real business. A source that instead uses generic knowledge includes a broad `database` domain; review its membership carefully before allowing self-onboarding.

## Relationships and generated SQL

The table-confirmation panel now offers a **database → schema → table** selector. For the starter mapping it shows:

```text
DatabaseA
  dbo
    Customers (crm_customers)
DatabaseB
  dbo
    Orders (sales_orders)
```

The model's suggested tables are preselected. Select individual tables or use a database/schema checkbox to select its permitted children; mixed selections are indicated. Only registered tables within the current domain/table permissions appear. The browser submits logical IDs, not arbitrary database names, and normal server-side version/scope checks remain authoritative. This selection takes place after the English question and before SQL generation; it does not create another connection or discover unconfigured databases. Older/ordinary source reviews remain compatible with flat selection.

SQL Server cannot declare native foreign keys spanning two databases. The optional `relationships` list supplies reviewed join metadata between logical IDs. Columns are checked against discovered metadata. These are operator declarations (`origin: operator_config`), not database-enforced constraints or proof of business correctness. Include complete tenant/company and entity keys when the relationship needs them. Do not assume an order total is rupees, cents or any other unit without a business definition.

The Schema Agent sees the permitted logical IDs, their physical identities, permitted columns and accepted relationships. It cannot inspect mappings outside the user's authorized domain union. Table selection/confirmation and business definitions use logical IDs, avoiding collisions when two databases contain a table called `Customers`. The SQL Agent can generate logical references or exact registered three-part names, using explicit SQL aliases.

Both forms are supported:

```sql
SELECT a.CustomerName, b.OrderTotal
FROM DatabaseA.dbo.Customers AS a
INNER JOIN DatabaseB.dbo.Orders AS b
  ON a.CustomerID = b.CustomerID;
```

```sql
SELECT a.CustomerName, b.OrderTotal
FROM crm_customers AS a
INNER JOIN sales_orders AS b
  ON a.CustomerID = b.CustomerID;
```

Before execution, logical references are expanded to trusted `[DatabaseA].[dbo].[Customers]` and `[DatabaseB].[dbo].[Orders]` paths. Protected column projections and configured row filters are applied to every base-table reference. Final SQL approval shows the actual expanded SQL, parameters and read scope. PII-restricted users cannot read `CustomerName` if it is classified; ask for non-sensitive IDs or obtain the required PII grant.

For a permitted business user, try: **“Show customer IDs and their order totals, joining CRM customers with Sales orders.”** The user needs both domains (directly or through groups), unless one granted domain already contains every required logical table. A selected UI domain remains a relevance hint within the authorized union. Database login grants alone cannot bypass app permissions.

## PII and row policies

Use the source-profile ID and logical table IDs consistently in business domains, PII classification and row-security configuration. For example, merge this source entry into the existing encryption configuration's `sources` object:

```json
"sqlserver_sales_crm": {
  "crm_customers": ["CustomerName", "Email"],
  "sales_orders": []
}
```

Adapt the column list to actual sensitive fields. Remote local AES-GCM decryption is not added by this feature.

A reviewed row-rule mapping might be:

```json
"sqlserver_sales_crm": {
  "crm_customers": {
    "mode": "filtered",
    "where": [{"column": "Region", "op": "in", "attribute": "regions"}]
  },
  "sales_orders": {
    "mode": "filtered",
    "parents": [{"table": "crm_customers", "columns": {"CustomerID": "CustomerID"}}]
  }
}
```

Merge that into the row-policy file's `sources` object; configure trusted group/user region attributes separately. Parent rules require the complete native parent primary key. For a composite key, include every key pair, such as both `TenantID` and `CustomerID`. Missing attributes and unmapped enabled rules deny rows. Policy-only parents may filter an allowed order table without granting direct access to the customer table. Their physical references are resolved from the same trusted mappings; a model-defined CTE cannot substitute for the policy table.

## Approval, upgrade and limitations

Restart the backend after changing source profiles or connection settings. Physical names, exposed schema, compiled protection and adapter capability are bound into the approval manifest. Changing a physical mapping changes the effective SQL/schema and invalidates old approval fingerprints; restored reviews must match the current plan and obtain renewed approval. Row-policy reload/revocation remains unchanged.

This mode rejects table hints, temporal clauses, column-renaming table aliases, table functions, unregistered objects and linked-server/four-part paths. The expanded SQL is bounded to 200,000 characters. Native isolation/consistency and runtime-role grants remain the database operator's responsibility. Multi-server federation, automatic credential provisioning, discovery of every database on a server, cross-database mapping support for other engines and app-managed remote encryption remain future work.

## Verification

Fifteen focused tests cover three-part/logical references, duplicate leaf names, parameters, CTEs/self-joins/unions, qualified columns, unsafe/unconfirmed objects, metadata relationships, hidden PII, composite-key cross-database row rules, policy-parent CTE spoofing, mapping fingerprints, source registration, permitted-only picker metadata, restored review locations and authenticated domain-grant/SQL-approval behavior. Frontend checks cover grouped display, mixed/group selection, legacy fallback and locking after submission. Generated T-SQL is parsed locally; protection semantics are also exercised against attached SQLite databases after dialect translation. No live model provider is used.

The complete backend/API regression suite passed 211 tests, and the Python dependency check passed. Live SQL Server/Azure SQL Managed Instance connections, actual role grants, certificates, reflection behavior and production performance still need deployment-specific verification; no remote endpoint/credentials were provided. See [Microsoft multipart-name syntax](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/transact-sql-syntax-conventions-transact-sql) and [Azure engine differences](https://learn.microsoft.com/en-us/azure/azure-sql/database/features-comparison).
