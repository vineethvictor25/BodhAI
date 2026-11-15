# Application strengths, limitations and trade-offs

Reviewed: 15 November 2026. This assessment describes the current implementation, not planned features. Operational details are in [UI.md](UI.md), [SECURITY.md](SECURITY.md), [PHASE_A.md](PHASE_A.md), [SOURCES_AND_DOMAINS.md](SOURCES_AND_DOMAINS.md) and [ENHANCEMENTS.md](ENHANCEMENTS.md).

## Current position

QueryDesk is a browser-based, read-only English-to-SQL application built with FastAPI, React/Node.js, LangChain and LangGraph. It is suitable for controlled data exploration and development of governed NLQ workflows. It has useful security and evaluation foundations, but it has not demonstrated production-scale accuracy, concurrency or independent security assurance.

The current local checks are 256 passing Python/API tests, 46 passing frontend tests, one passing Playwright UI-to-API E2E test, a successful frontend build and a clean Python dependency check. The browser test uses a temporary SQLite sample and deterministic workflow; it makes no provider calls. These results are evidence of covered behavior, not an accuracy percentage, multi-process/load-test result or security certification.

## Pros and cons by area

| Area | Strength | Limitation or cost |
| --- | --- | --- |
| Business-user experience | Users ask in English and see an answer, supporting reason, selected tables, SQL and native result tables with horizontal scrolling and exact numeric display. Browser users install nothing. | Users must still understand the business question and review results. Result limits/clipping apply, and saved history may omit raw rows. The interface offers AI-recommended result charts, but not custom dashboards, a manual chart editor or result exports. |
| Workflow transparency | Specialized agents have clear responsibilities; a central graph controls handoffs, validation and repair. | The standard successful path normally needs six model calls. More calls increase latency, token use, provider cost and opportunities for failure. |
| SQL safety | Read-only access, bound filter parameters, table/function restrictions, compilation checks and single-use execution approval provide multiple controls. | Safe, executable SQL can still use the wrong metric, join or filter. The generated explanation can also be wrong. These controls do not prove business correctness. |
| Human review | Table confirmation is followed by separate approval of effective SQL, bound values and read scope before execution. Planning/example checks use metadata only; repaired queries require fresh approval. Waiting releases the worker. | Review adds effort and requires some SQL/business familiarity. It does not prove semantics. Non-PII SQLite reads use protected direct execution; classified PII can require bounded local copying. |
| Business context | Definitions, accepted relationships and reviewed examples help ground generation. | Someone must curate and maintain them as schemas and business rules change. Ambiguous or undocumented metrics still need clarification. |
| Domains | Admins can define domains in the UI/config, or review AI-generated drafts. Domains guide relevance while permissions define the access boundary. | Incorrect domain table lists can give overly broad or insufficient access. Overlapping domains share access to their common tables; they do not isolate rows. |
| Retrieval and pruning | Deterministic keyword retrieval has no embedding-service dependency. Column pruning retains keys and required metric/example columns. | Keyword retrieval can miss synonyms and unfamiliar phrasing. Vector/hybrid retrieval is not implemented. Remote validation may conservatively restore all columns of referenced tables, reducing pruning benefits. |
| Model flexibility | Endpoint, API key and model settings are configurable; compatible providers can use the same integration. | Compatibility requires the supported OpenAI-style API and tool-calling contract. Arbitrary provider-native APIs and models are not automatically supported. Quality, quota and response behavior vary by model. |
| Data sources | SQLite works locally; PostgreSQL, MySQL, SQL Server/Azure SQL, Oracle, Snowflake, Redshift and BigQuery adapters included in the main requirements provide scoped metadata, dialect SQL and protected projections. BigQuery includes approved billed-byte controls. | Live remote-server/IAM/TLS verification is pending. SQL Server/Snowflake/BigQuery require restricted database/IAM grants rather than read-only transactions. New vendor syntax may be rejected. One query uses one source. SQL Server can register multiple databases/schemas on the same instance within that source; cross-server/cross-engine federation is not implemented. |
| Authentication | First-run admin setup, restricted self-onboarding, password hashing, sessions, CSRF checks and revocation are implemented. | This is local account management. SSO, MFA, email verification and a self-service password-reset workflow are not implemented. The supplied scripts do not deploy a shared HTTPS service. |
| Onboarding | A new member can choose one starting domain; admins can grant more access later. | Registration is self-service rather than approval-gated. A new member can choose an offered onboarding domain, so its initial access must be appropriate for self-selection. |
| Group permissions | Domain grants are inherited; group PII denial overrides member allows. Configurable row attributes further restrict region/country/tenant rows and inherited parent records. | Row security ships disabled until mapped/enabled. Restrictions intersect and policy changes invalidate old work. File rules need operator review; live warehouse/native-role verification remains pending. |
| PII authorization | Protected columns are excluded before unauthorized SQL executes, covering aliases, expressions, predicates and wildcard queries. | Classification is configured, not automatically discovered. Missing or incorrect classification can leave sensitive columns unprotected. Unmapped external sources are blocked for members without PII permission. |
| Local encryption | AES-GCM field encryption, encrypted backups and encrypted application snapshots/comments are available. Keys stay on the server. | Encryption is disabled by default. Business-source columns remain plaintext until explicitly migrated. Local field migration supports constrained SQLite TEXT columns; it does not encrypt external warehouses. Key rotation, keyrings and cloud key-management integrations are not implemented. |
| LLM data exposure | Result-cell tokenization is supplemented by endpoint/model/source approval, local pattern checks and separate detected-PII sender grants before every model request. | Detection is incomplete and can produce false positives; questions, parameters, metadata and examples are not comprehensively anonymized. Provider approval requires operator review. Remote models still receive permitted context. |
| Evaluation | Repeated result-based checks, frozen answers, bounded provider retries, atomic checkpoints and guarded resume make regressions more visible. Both result correctness and a strict normalized SQL/reference match are reported. | The bank is small and synthetic: 30 development plus 37 held-out questions. SQL matching is structural, not proof of semantic equivalence. Some patterns overlap approved examples; independent business review and broad live accuracy evaluation remain pending. |
| Feedback | Feedback is associated with the question/query and saved for review, rather than immediately changing prompts. | Feedback does not automatically correct the model. An operator must investigate and deliberately curate improved examples or evaluation cases. |
| Operational visibility | Admin-only queue gauges, bounded stage/provider/adapter latency histograms and reported/unknown token totals are available. | Metrics are process-local and reset on restart; central time-series monitoring, alerts and billing reconciliation remain future work. |
| Submission reliability | Owner-scoped durable idempotency keys return the original job for retries and reject mismatched, deleted or expired submissions. | Clients must supply/reuse keys; the browser retains uncertain keys only within the mounted app. This is not a distributed exactly-once guarantee. |
| Load/failure exercises | An isolated API harness uses concurrent synthetic users, scripted provider outages, actual SQLite deadlines and cancellation/replay checks. | These runs do not certify production network behavior, live warehouse capacity or provider quotas. See [operations and reliability](OPERATIONS_AND_RELIABILITY.md). |
| Persistence | Query snapshots, feedback and safe review checkpoints have fixed expiry, owner-scoped deletion and protection against late-worker resurrection. Reviews can be restored after restart with fresh permission/plan checks and approval. | Running stages and uncertain executions are not replayed. Raw rows are omitted by default; questions, SQL and checkpoint metadata can still be sensitive. Legacy migration and report/audit/backup retention remain operator concerns. |
| Exact numbers | Native warehouse decimals and large integers retain exact values through JSON, encrypted records, evaluation and browser formatting. | Existing floating-point source values and historical precision loss cannot be repaired by serialization. Live warehouse round trips remain unverified; SQLite financial arithmetic needs reviewed minor-unit definitions. |
| Concurrency and limits | A bounded queue, explicit query/result limits and non-blocking review segments prevent idle human reviews from holding the workflow worker. Tests cover two authenticated users in one process and safe review/history recovery after restart. | Worker concurrency and active/queued and per-user limits are configurable. Defaults are one worker, three runnable segments and 50 unfinished jobs; long calls serialize with one worker. The job queue/continuations are process-local; multiple API workers sharing the SQLite records file are unsupported. Distributed dispatch and load validation remain open. |
| Protected SQLite reads | Supported non-PII queries use direct source-side projections/predicates, read-only transactions and a native authorizer. Classified PII retains local handling with approved configurable resource limits. | The PII fallback still copies data with a 100,000-row/20 MB ceiling and additional input/cell/count/time limits. Payload budgets do not bound total process memory; cooperative deadlines cannot preempt OS I/O or atomic cryptographic operations. Unsupported SQL is rejected. Direct reads require workload performance testing. |
| Extensibility | Agents, source adapters, policy enforcement, model integration and UI are separated into understandable modules. | Extensions still require implementation and regression testing. A configurable field is not evidence that every backend, encryption mechanism or security policy already works. |

## Suitable use cases

- A controlled pilot where business/BI users can compare generated answers with known reports.
- Read-only exploration of a curated schema with maintained definitions and examples.
- Developing and evaluating domain-guided, permission-aware SQL workflows.
- Demonstrating authentication, inherited PII permissions and local SQLite encryption using synthetic data.

## Where additional work is needed

- **High-impact or official reporting:** require reviewed definitions, independently checked reference answers and a measured live accuracy baseline. A plausible explanation is not sufficient evidence.
- **Regional/country/tenant access:** configure and validate the implemented row policies against the actual deployment. Operators must review mappings, composite ownership paths, source types/collations and global-table declarations. Application filtering is not database-native security for external clients.
- **Many simultaneous users:** redesign the waiting/worker model and validate concurrency, isolation, load and recovery behavior.
- **Large datasets:** direct SQLite behavior is tested above the previous copy ceiling. Broader performance and live warehouse behavior still need representative schemas, plans, resource controls and provider-context measurements.
- **Shared or public hosting:** review HTTPS, cookie/host/origin/proxy settings, host permissions, operational monitoring, account lifecycle and security testing. The supplied loopback configuration does not perform this deployment.
- **Sensitive-data operations:** review classifications and model-bound inputs, protect encryption keys/backups and validate the required provider-native or local encryption mechanisms.

## How to interpret the evidence

The recorded small live held-out evaluation ran one question twice: one run returned the correct result; the other failed after a bounded provider rate-limit retry. This shows why provider reliability and correctness are reported separately. It does not establish a useful overall accuracy percentage for the application.

The sample contains 19 related tables, 25 orders, 12 customers and synthetic PII contacts. Tests against it establish specific behaviors and expected totals; they do not establish performance or correctness on an organization's real data. See [ENHANCEMENTS.md](ENHANCEMENTS.md) for the evaluation method and [SECURITY.md](SECURITY.md) for the security boundaries.

## Priority improvements

See [HARDENING_PLAN.md](HARDENING_PLAN.md) for the detailed eight-issue plan and its dependency-based delivery order. It expands the priorities below; no fixes are implied by that document's existence.

| Priority | Improvement | Benefit |
| --- | --- | --- |
| 1 | Independent review of definitions/held-out answers and broader repeated live evaluation | Establish a credible correctness and reliability baseline. |
| 2 | Live registered-warehouse integration checks on representative schemas | Replace mocked connector evidence with end-to-end evidence. |
| 3 | Deployment-specific row-policy review and live warehouse/runtime-role verification | Validate the configured isolation and operational assumptions on real sources. |
| 4 | In-flight stage recovery, durable dispatch and broader multi-user load/revocation tests | Build on implemented review-checkpoint recovery without replaying uncertain execution. |
| 5 | Deployment/account lifecycle hardening and operational recovery procedures | Prepare for the chosen shared-host environment. |
| 6 | Evaluate hybrid retrieval and managed encryption adapters against concrete needs | Improve schema/example discovery and key operations where measurements justify the added complexity. |

Optional result visualizations now support agent-selected Plotly, Matplotlib and Seaborn renderers. Recommendations use profiles without cell values; chart geometry has numeric/point limits and remains an interpretation of returned data. Other libraries and map/quality-specific adapters are future work. See [VISUALIZATIONS.md](VISUALIZATIONS.md).
