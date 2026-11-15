# Configurable row-level access

Implemented on 14 November 2026. These are application-enforced policies for authenticated browser queries, layered on source/domain/table permissions and PII controls. They apply to configured connected databases, not just the sample database. SQLite enforcement is tested end to end; PostgreSQL/MySQL rewriting is tested on a local SQL runtime. Live warehouse verification is still pending. This is not database-native RLS for other clients using the same database credentials.

## Configuration and activation

The server reads:

```dotenv
ROW_SECURITY_ENABLED=false
ROW_SECURITY_FILE=row_security.json
```

Setup copies `row_security.example.json` when the local file is absent. The local policy is ignored by Git; the example is packaged. Existing environment values are preserved. The feature ships **disabled** so existing access is not silently replaced by guessed restrictions.

To enable it:

1. Configure the source/domain and PII mappings as usual. Row rules do not grant table or PII permission.
2. Review the local policy's table rules and allowed values. The example covers all 19 sample tables, including customers, orders, payments, inventory and related records.
3. In **Access and security**, create the desired groups and copy their displayed **Policy ID** values into `attributes.groups`. Assign members to these groups. Example keys such as `south_readers` do not create authentication groups automatically.
4. For a direct user restriction, copy the displayed **Account ID** into `attributes.users`. This must be the server-issued 32-character ID, never a username. New users cannot supply identity attributes, group membership, regions or tenants during registration or question submission.
5. Set `ROW_SECURITY_ENABLED=true` and restart the backend once to load the environment change. New questions then use the policies. Environment/source-profile changes require restart; valid edits to the JSON policy reload during normal authenticated API requests without restart.

`business_users` has no allowed-region attributes in the example. Therefore a new member sees no protected rows until an administrator assigns matching attributes/groups. Explicitly global tables remain visible within existing domain/table grants. Assigning attributes to `business_users` deliberately creates a shared onboarding baseline for all its members; use dedicated groups when people need different scopes.

Use atomic file replacement when editing. Invalid or missing enabled configuration blocks data access rather than falling back to unrestricted results. Valid changes update the permission signature, cancel waiting reviews and invalidate old approvals, history access stamps and recovery checkpoints. All sources' outstanding work can be invalidated by a policy edit; make coordinated changes. Already-started reads/provider requests cannot be undone, but stages and result delivery recheck access.

## Rule format

The configuration has `version: 1`, an explicit `admin_bypass` boolean, optional subject `attributes`, and per-source table mappings in `sources`. Each source key must match its data-source profile ID.

This example can apply to an added PostgreSQL/MySQL/SQLite source whose profile ID is `analytics`:

```json
{
  "version": 1,
  "admin_bypass": true,
  "attributes": {
    "groups": {
      "regional_readers": {
        "sales_regions": ["South"],
        "country_codes": ["IN"],
        "tenant_ids": [101]
      }
    },
    "users": {}
  },
  "sources": {
    "analytics": {
      "customer_dim": {
        "mode": "filtered",
        "where": [
          {"column": "region_code", "op": "in", "attribute": "sales_regions"},
          {"column": "country_code", "op": "eq", "attribute": "country_codes"},
          {"column": "tenant_id", "op": "in", "attribute": "tenant_ids"}
        ]
      },
      "sales_fact": {
        "mode": "filtered",
        "parents": [
          {"table": "customer_dim", "columns": {"tenant_id": "tenant_id", "customer_id": "customer_id"}}
        ]
      },
      "product_dim": {"mode": "global"},
      "private_notes": {"mode": "deny"}
    }
  }
}
```

Replace the illustrative group ID, source ID, table/column names and values with the actual deployment mappings. There is no retail-schema assumption in the policy engine. Attribute names are arbitrary simple identifiers; different sources can use different attribute keys and coding systems.

| Setting | Meaning |
| --- | --- |
| `mode: global` | All rows are available, subject to existing source/domain/table/PII permissions. |
| `mode: deny` | No rows are available. Unmapped tables and sources also default to deny. |
| `mode: filtered` | Every local predicate and parent restriction must hold. At least one restriction is required. |
| `where` | Compare a local column against a trusted attribute with `in` or `eq`. No SQL fragments, functions, executable templates or client-supplied claims are accepted. |
| `parents` | Inherit the referenced table's complete policy through correlated EXISTS joins. Map child columns to parent columns. Every parent must have its own explicit rule. |
| `admin_bypass: true` | Administrators bypass row restrictions, preserving the original all-data administrator behavior. Existing read-only/source/PII controls remain enforced. |
| `admin_bypass: false` | Administrators also require applicable attributes and row rules. This does not remove their PII permission or account-management role. |

Relationships must include the **complete parent primary key**, including all composite-key components. Nullable/unmatched ownership does not satisfy the parent restriction. Parents can lie outside a member's queryable domain if they remain inside the server's source table allowlist: they are trusted filtering dependencies, not newly queryable tables. Cycles, unsupported identifiers and excessive expression size/depth fail closed. The operator must review relationship meaning; the system does not infer tenant ownership automatically.

Predicate/join columns must exist and must not be PII-classified. Encrypted/PII fields cannot serve as policy keys in this version. Operators should use normalized non-PII region/country/tenant identifiers. Comparisons follow source types and collation; review ambiguous string identifiers and collations before treating them as separate security identities.

## Group and user combination

Allowed values within one attribute list are alternatives: `regions: ["South", "North"]` allows either value for an `in` predicate. Contributions to the same attribute from different groups and the direct account entry **intersect**. A direct account entry cannot override a restrictive group. Groups without that attribute contribute no additional constraint. An empty intersection/list or a missing required attribute denies matching rows. An `eq` predicate requires exactly one effective value.

For example, groups allowing `["South", "North"]` and `["South"]` resolve to `["South"]`. Separate groups allowing only South and only North resolve to no rows. To grant both, use one group containing both values. Predicates and parent relationships combine with AND. Wildcards and arbitrary OR expressions are not supported; `"*"` would be an ordinary string value. Attribute values are bounded strings or signed 64-bit integers.

## Enforcement, approval and recovery

For [mapped SQL Server cross-database sources](SQLSERVER_CROSS_DATABASE.md), policy table names remain logical IDs such as `crm_customers` and `sales_orders`. Parent predicates are resolved to the registered physical databases/schemas, including full composite-key joins. This keeps configured region/tenant restrictions active across the same-instance database boundary without exposing the parent table as a directly queryable user table.

SQLite now uses [direct protected reads](DIRECT_READS.md) for supported queries that do not need classified PII handling: every table is projected and row-filtered before joins/aggregation, and execution stays on a read-only source connection with a native authorizer. Queries referencing classified PII retain the isolated local path; its 100,000-row/20 MB ceiling still applies. Direct reads avoid that copy ceiling but retain execution/result limits and need workload-specific performance testing.

Registered remote SQL adapters inject each base table's predicate into its protected derived-table projection before joins and aggregation. Namespaces are qualified, attributes are bound parameters and the `__rls_` parameter namespace is reserved. This path also filters every self-join/CTE/union base-table reference. Database credentials still need appropriate read-only grants. Registered engines include PostgreSQL, MySQL, SQL Server, Oracle, Snowflake, Redshift and BigQuery; see [WAREHOUSE_CONNECTORS.md](WAREHOUSE_CONNECTORS.md). Additional unregistered dialects require reviewed adapters and fail closed.

Planning stays metadata-only. Final review shows effective SQL/parameters, read method and a separate **Row-level access** section. Direct SQLite and warehouse reads show the rewritten source query; local PII processing shows its bounded copy scope and filters. The policy signature, compiled restrictions and execution method are fingerprinted. Restored SQL must match the current manifest and receive a fresh approval; policy changes invalidate old recovery access stamps. Private physical dependency schemas are not returned in model-facing plans.

Queries restricted by row rules receive a deterministic access-scope disclosure in the answer/reason, even if the response model omits one. Counts/totals are scoped results, not a claim about all underlying data. PII permission never removes row restrictions. Result-cell tokenization, provider policy, history retention and checkpoint encryption remain independent controls.

The CLI, evaluation tools and direct adapter calls remain trusted operator interfaces; they do not impersonate browser users. Database-native policies, external-client enforcement, SSO claim adapters, per-request identity databases, a policy-editing UI and additional predicate operators are future extensions. Group membership is managed in the existing admin UI; rule/attribute values are currently managed in the server file. No user data or membership was changed to activate a guessed restriction for this implementation.

## Verification

Tests cover a separate tenant schema with overlapping customer/order IDs, composite parent keys, region/country/tenant intersections, NULL ownership, missing attributes, denied/unmapped sources, global dimensions, aggregation and join/CTE/union/OR bypass attempts, reserved parameters, PII exclusion/encryption, metadata-only approval manifests, hot reload, pending-work revocation and result withholding after a policy change. An authenticated API test queries an additional connected SQLite source using its own mapping. PostgreSQL/MySQL generated projections execute on a local SQLite SQL runtime. Added warehouse dialects also have translated SQLite-runtime checks for protected projections and composite-key parent policies. Live remote-engine verification and independent security review remain pending. No live model-provider calls were made.

Historical checks at completion of the row-security increment: 136 backend/API tests and 21 frontend tests passed, the production frontend build passed, and the dependency check was clean. These are covered-behavior checks, not a production isolation certification or load benchmark.

For the latest complete regression count, see [README verification](README.md#verification).
