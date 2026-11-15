# Synthetic analytics lab

The lab adds **399,408 fictional rows across 20 related tables**, including **1,200 customers and 59,431 orders**. Observed history covers **1 January 2023 through 31 October 2026**. The original retail sample is unchanged. Money is stored exactly as integer paise; divide by 100 for INR display.

## Use it in the UI

The local installation has a new source: **Analytics lab · local combined mirror** (`analytics_lab`). Restart the API after changing source configuration, refresh the browser, and select this source. Its business domains are Commerce, Customer and marketing intelligence, and Predictive analysis lab. Domains guide discovery; existing permissions still decide access.

Ask a question, confirm the tables, review and approve SQL, then select **Visualize this result**. A single monthly metric supports a line chart; categorical totals support bars; two numeric measures support a scatter plot; one aggregate supports a KPI. Charts use the returned rows and existing chart limits. Request one series or a narrower date range if the result is unsuitable. SQL approval and PII/row restrictions apply normally.

Administrators retain their configured privileges. The generator does not grant members access to this new source or domains. Grant source/domain permissions through the existing administration workflow. With row security enabled, members also need a reviewed `regions` scope such as `South` in their group/user attributes. Missing regional scope denies protected fact rows. The generated source rules propagate regions through customers, orders, campaign spend, snapshots, targets and forecasts. Shared dimensions are global. Email and phone are classified as PII; the generator does not automatically encrypt the synthetic seed files. Existing local PII configuration and permitted decryption/masking behavior remain in force.

## Three files, two database arrangements

Generated files live under `data/analytics_lab/`:

| File | Contents and purpose |
| --- | --- |
| `commerce.db` | Commerce database: orders, line items, products, customers, payments, refunds, shipments, calendar, regions and monthly sales |
| `intelligence.db` | Related second database: segments, campaigns, daily ad spend, support tickets, business events, targets, customer features/outcomes and forecasts |
| `analytics_mirror.db` | Combined local source containing all 20 tables, with `commerce_` and `intelligence_` prefixes and cross-domain foreign keys |
| `sqlserver_setup.sql` | Seed script for two new databases on one SQL Server instance |
| `sources.local.json` / `sources.sqlserver.json` | Local mirror profile and native mapped SQL Server profile |
| `knowledge/` / `knowledge_sqlserver/` | Business definitions, domain metadata and 12 reference SQL examples in the relevant dialect |
| `reference_answers.json` | Expected results for the 12 reference queries, generated from the fixed dataset |
| `pii.sources.json`, `row_security.sources.json`, `model_policy.sources.json` | Source-specific policy snippets for reviewed configuration merges |
| `manifest.json` | Settings, row counts, validation results and SHA-256 hashes of all three SQLite databases |

The app queries the combined mirror locally. **This is not SQLite federation.** An offline test attaches the two separate files and verifies that cross-file joins match the mirror. Native app joins across databases use the existing mapped **SQL Server same-instance connector**, after deployment of the two databases there. No remote warehouse was created or queried by this change.

## Relationships and analytical grains

| Relationship | Join keys / grain |
| --- | --- |
| Customers → segments | `commerce_customers.segment_id = intelligence_segments.segment_id` |
| Orders → customers | `customer_id`; customers also reference `region_id` |
| Orders → campaigns | Nullable `campaign_id`; campaigns reference `region_id` |
| Order children | Items, payments, refunds and shipments reference `order_id`; refunds also reference `payment_id` |
| Products → categories | `product_id` on items, `category_id` on products |
| Campaign daily spend → campaigns | `campaign_id`; daily grain `(campaign_date, campaign_id)` |
| Support / customer features → customers | `customer_id`; feature grain `(snapshot_month, customer_id)` |
| Features → next-month outcomes | **Both** `snapshot_month` and `customer_id` |
| Monthly sales → targets | **All three** of `month_date`, `region_id`, `category_id` |
| Forecasts → monthly actuals | `target_month = month_date`, `region_id`, `category_id`; also select an origin/horizon before comparing |

In the split databases, physical names omit the domain prefix: for example `AnalyticsCommerce.dbo.orders` and `AnalyticsIntelligence.dbo.segments`. The SQL Server profile maps those locations to stable logical table IDs. SQLite cannot declare foreign keys across files; those relationships are checked in the combined mirror and supplied in the native profile.

Avoid joining several independent child facts directly: payments, items and refunds can multiply each other's rows. Aggregate each to the required join grain first. `commerce_monthly_sales.completed_orders` counts participating orders **per category** and cannot be summed across categories to obtain unique orders; use completed `commerce_orders` for an overall count or average order value.

## What the synthetic data supports

There are four regions, three customer segments, four product categories, 48 products, multiple acquisition/order channels, seasonal demand, gradual growth, discounted items, split and failed payments, refunds, unresolved tickets and undelivered shipments. A June 2024 disruption and November 2025 promotion create visible departures from normal patterns. The calendar extends into forecast target months.

Metric definitions are documented in the source catalog:

- **Completed net product sales:** completed order sales after item discounts, before refunds, tax and shipping.
- **Net collected cash:** captured payments minus refunds; captured amounts include tax and charged shipping. Failed attempts are excluded.
- **Average order value:** completed net product sales divided by unique completed orders.
- **Product margin before refunds:** completed net product sales less historical line cost. `cost_cents` is the total line cost, not a unit cost. Tax, shipping and refunds are excluded from this margin definition.
- **ROAS:** attributed completed net product sales divided by campaign spend, with the facts aggregated separately. It measures synthetic attribution, not causal campaign uplift.

Full-history checksums for the default seed include net collected cash of **INR 85,324,123.90** and completed net product sales of **INR 75,951,203.28**. Region/segment filters and restricted users will see different totals. Integer source values remain exact; SQL examples using `/100.0` produce ordinary display numbers, so use integer aggregates or appropriate exact decimal casts for financial reconciliation.

## Questions to try

| English question | Analysis / visualization |
| --- | --- |
| Show monthly completed net product sales in INR from January 2023 through October 2026. | 46-point trend line |
| Compare completed net product sales by region over the full observed period. | Four-region bar chart |
| What is total net collected cash after refunds in INR? | KPI |
| Show average completed order value by month for 2025, before refunds, tax and shipping. | Monthly KPI trend |
| Compare completed sales and unique order counts by customer segment across both domains. | Cross-database join; table or one selected metric |
| Which 20 campaigns have the highest ROAS? Aggregate sales and spend independently. | Cross-domain CTEs; bar chart of ROAS |
| Show monthly sales-target attainment percentage for the South region in 2025, aggregated across categories. | Composite-key join; line chart |
| Show monthly sales, trailing three-month average and year-over-year growth. | Window functions; table or one metric per chart |
| Track monthly active account percentage for the January 2023 acquisition cohort. | Cohort retention line |
| Compare product margin before refunds by customer segment. Sum historical line cost once per line and show refunds separately. | Five-table analysis; margin bar chart |
| For October 2026, return recency days and monthly net product sales in INR for up to 400 customers with known recency. | Scatter plot; avoid selecting PII |
| Show observed next-month inactivity percentage for historical labeled snapshots. Join features and outcomes on both customer and snapshot month. | Temporal label analysis |
| Compare one-month-ahead forecast units with observed monthly units by target month. Keep unknown actuals NULL. | Backtest table/scatter; line chart of one measure |
| For the October 2026 forecast origin, show total predicted units for each of the next three months. | Future baseline trend |
| Compare average delivery duration by carrier for completed deliveries in 2025. | Date arithmetic and joins; bar chart |
| How did daily completed order counts change around the June 2024 disruption? Include days with zero orders. | Calendar left join; event trend |

The reference file provides 12 verified SQL/result pairs. These are deterministic data fixtures; generating the correct query repeatedly through an LLM still requires the app's evaluation workflow. No paid model calls are required to generate or validate the lab.

## Predictive analysis and limitations

Customer features are recorded **as of snapshot month end**: tenure, current-month completed orders/sales/units, recency, prior-three-month orders, prior-year monthly sales and recent support activity. Unknown recency and unavailable prior-year history are NULL, rather than fabricated zeros. Labels are stored separately as next-month orders, sales and inactivity; the final observed month has no outcome label.

Training/evaluation splits must respect both the feature date and `label_month` availability. An inactivity label is a synthetic next-month proxy, not a claim of permanent churn. Customer IDs/PII should not become model features. The data provides a starting point for predictive experiments; the app does not train a new predictive model automatically.

Stored forecasts use a seasonal prior-year baseline scaled by recent year-over-year demand, bounded to avoid extreme growth factors. Forecast origins begin in January 2024; each has one-, two- and three-month horizons. Training ends at the origin month end. The latest origin is October 2026, targeting November 2026–January 2027. Unknown future actuals must remain NULL and must be excluded from accuracy metrics. Scenario bounds of ±20% are illustrative, **not calibrated confidence intervals**. Compare a fixed horizon and avoid counting overlapping forecasts as independent observations.

The generator intentionally shares latent patterns across related data. Results illustrate joins, analysis and chart selection, not real-world forecast quality or causal relationships. It includes synthetic contacts and predictable identifiers; do not use them as production identities.

## Rebuild or create variants

Run from `langgraph_impl` using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m langgraph_impl.analytics_demo --register-local
```

The default fixed seed is `20261115`. Repeating the command checks database hashes/settings and reuses the existing lab; it refuses to overwrite edited or incomplete datasets. To generate a variant, choose a new directory:

```powershell
.\.venv\Scripts\python.exe -m langgraph_impl.analytics_demo --directory data/analytics_variant --seed 42 --customers 2000 --months 48 --orders-per-day 40
```

Supported bounds are 100–5,000 customers, 12–60 months and 5–100 base orders/day; actual volumes vary with trend and seasonality. The date range always starts in January 2023. Longer variants can extend beyond the default observed cutoff. Register variants manually with distinct source IDs rather than replacing the installed lab profile. Registration preserves existing provider approvals, member grants, encryption flags and credentials. Custom/external configuration files may require manual merges; registration refuses conflicting lab mappings.

## Native SQL Server cross-database testing

1. Review `sqlserver_setup.sql`. Run it through **sqlcmd** (use failure-on-error behavior) or SSMS with **SQLCMD Mode enabled**. The `:ON ERROR EXIT` directive is required: stop on the existing-database guard or any other error. The script creates new databases `AnalyticsCommerce` and `AnalyticsIntelligence`, seeds tables and adds indexes. It never drops an existing database. Provisioning requires suitable server privileges; the app's runtime login should have read-only permissions on the mapped tables in both databases.
2. Merge the entry from `sources.sqlserver.json` into the installation's configured source profile list. Preserve the mappings and reviewed relationships. This profile uses SQL Server-specific example SQL.
3. Set `ANALYTICS_LAB_SQLSERVER_URL` in `.env` using the existing connector's `mssql://` format and a login allowed to read both databases. Do not commit credentials. Keep verified TLS enabled for normal deployments; follow [TLS configuration](TLS_CONNECTIONS.md) for certificate settings.
4. Merge only the `analytics_lab_sqlserver` entries from the PII, row-security and model-policy snippets into the corresponding configuration files' `sources` objects. Review the source classification and use an already approved provider profile. Do not bypass provider approval or copy group/user grants blindly.
5. Restart the API, grant the intended source/domain access, and select **Analytics lab · two SQL Server databases**. Table confirmation groups the permitted tables by physical database/schema. Review the expanded SQL before execution.

Expected native join shape (not a substitute for app approvals):

```sql
SELECT s.segment_name, COUNT(*) AS completed_orders,
       SUM(CAST(o.net_sales_cents AS DECIMAL(28,0))) / 100.0 AS net_product_sales_inr
FROM AnalyticsCommerce.dbo.orders AS o
JOIN AnalyticsCommerce.dbo.customers AS c ON c.customer_id = o.customer_id
JOIN AnalyticsIntelligence.dbo.segments AS s ON s.segment_id = c.segment_id
WHERE o.status = 'completed'
GROUP BY s.segment_name;
```

Local tests verify split-file joins, mappings, T-SQL parsing, exact integer reconciliation, feature/forecast cutoffs, regional filtering, read-only reference query execution and chart rendering. Actual SQL Server execution, server permissions and transport still need verification against the operator's SQL Server instance. Cross-server/cross-engine federation remains outside current support.
