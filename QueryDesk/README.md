# NLQ to SQL — LangChain and LangGraph

There is one implementation: [langgraph_impl](langgraph_impl/README.md).

It contains the agents, graph orchestration, provider integration, SQLite database, configuration, virtual environment and tests.

## Documentation

| Guide | Contents |
| --- | --- |
| [Application overview](langgraph_impl/README.md) | Architecture, configuration and CLI |
| [Synthetic analytics lab](langgraph_impl/ANALYTICS_LAB.md) | 20 tables, 399,408 rows, related databases, trends, KPIs and predictive fixtures |
| [Pros, cons and suitable use cases](langgraph_impl/PROS_AND_CONS.md) | Strengths, trade-offs, current limits and priorities |
| [Hardening implementation plan](langgraph_impl/HARDENING_PLAN.md) | Eight issues, dependencies, migrations and acceptance gates |
| [Phase A implementation](langgraph_impl/PHASE_A.md) | History expiry/deletion, exact numbers, model policy and upgrade steps |
| [Final SQL approval](langgraph_impl/SQL_APPROVAL.md) | Review the effective query before business-data reads |
| [Non-blocking reviews](langgraph_impl/NON_BLOCKING_REVIEWS.md) | Release the worker while users review; restart and capacity limits |
| [Restart recovery](langgraph_impl/RESTART_RECOVERY.md) | Restore safe review checkpoints with renewed approval |
| [Configurable row security](langgraph_impl/ROW_SECURITY.md) | Per-source region/country/tenant rules, group attributes and activation |
| [Direct protected reads](langgraph_impl/DIRECT_READS.md) | Source-side SQLite reads and bounded PII fallback |
| [Selective PII copying](langgraph_impl/COPY_REDUCTION.md) | Required columns/keys and safe source conditions without weakening approval |
| [Bounded encrypted PII](langgraph_impl/BOUNDED_PII.md) | Configurable processing limits, approval and safe failure |
| [Future connector contract](langgraph_impl/CONNECTOR_CONTRACT.md) | Shared governance and requirements for new database adapters |
| [Browser UI](langgraph_impl/UI.md) | Setup, startup and recorded verification |
| [Security](langgraph_impl/SECURITY.md) | Accounts, groups, domain/PII permissions and encryption |
| [Sources and domains](langgraph_impl/SOURCES_AND_DOMAINS.md) | Connectors, domain configuration and AI drafts |
| [Cloud and on-premise warehouses](langgraph_impl/WAREHOUSE_CONNECTORS.md) | SQL Server, Oracle, Snowflake, AWS Redshift, GCP BigQuery and configuration |
| [SQL Server cross-database joins](langgraph_impl/SQLSERVER_CROSS_DATABASE.md) | Explicit same-instance table mappings, domains, permissions and approval |
| [Warehouse TLS configuration](langgraph_impl/TLS_CONNECTIONS.md) | TLS 1.2+ default, trusted certificates and optional development plaintext |
| [Evaluation and feedback](langgraph_impl/ENHANCEMENTS.md) | Question banks, persistence, retries and review |
| [Sample data](langgraph_impl/DATASET.md) | Relationships, synthetic contacts and verified totals |
| [Roadmap](langgraph_impl/ROADMAP.md) | Implemented features and remaining work |

## Run from this folder

For the browser UI, see the [UI guide](langgraph_impl/UI.md):

```powershell
.\langgraph_impl\setup-ui.ps1
.\langgraph_impl\start-ui.ps1 -OpenBrowser
```

```powershell
.\langgraph_impl\run.ps1 "How many orders are there?" --json
.\langgraph_impl\run.ps1 "Show successful payment totals by customer in INR." --json
.\langgraph_impl\run.ps1 --schema
```

Edit **langgraph_impl/.env** for model/provider settings. Model-backed requests also require operator approval of an endpoint/model/source profile in **langgraph_impl/model_policy.json**; the supplied policy starts unapproved. See [Phase A](langgraph_impl/PHASE_A.md) for this setup and existing-history migration. The sample database is **langgraph_impl/data/sales.db** and contains **25 orders across 19 related tables**. See the [dataset guide](langgraph_impl/DATASET.md) for relationships, metric definitions and questions to try.

For a fresh environment, run `.\langgraph_impl\setup.ps1` first. It is already configured in this workspace.

## Verify

```powershell
.\langgraph_impl\.venv\Scripts\python.exe -m unittest discover -s langgraph_impl/tests -v
.\langgraph_impl\.venv\Scripts\python.exe -m pip check
```

The app includes [business definitions, domain-guided routing, approved SQL retrieval, column pruning and repeated evaluation](langgraph_impl/ENHANCEMENTS.md). Its question bank contains 30 development and 34 held-out cases. Answered-case reference SQL is checked against the sample; expected clarification/refusal behavior needs generated-query evaluation. Provider rate limits can temporarily block live requests and are recorded as failures.

```powershell
.\langgraph_impl\run.ps1 --evaluate --reference-only --report outputs/development-references.json
.\langgraph_impl\run.ps1 --evaluate --case order_count --repeat 3 --pause-seconds 60 --report outputs/order-count-live.json
```

Choose a new report filename for each new evaluation, or use `--resume` with matching settings.
