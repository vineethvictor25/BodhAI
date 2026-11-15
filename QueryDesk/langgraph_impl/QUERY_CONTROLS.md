# Models, capacity, editing and cancellation

See [operations and reliability](OPERATIONS_AND_RELIABILITY.md) for live admin metrics, durable submission keys and isolated load/failure tests.

For a policy JSON with Groq, OpenAI, Claude and Gemini profiles, see [multiple-provider policy examples](MODEL_POLICY_EXAMPLES.md).

## Per-agent models

Start with the [step-by-step agent model configuration guide](AGENT_MODEL_CONFIGURATION.md) for a complete `.env`, matching policy JSON, precedence rules and a local verification command.

Configure role overrides in this application's `.env`. Credentials stay server-side.

| Agent | Prefix |
| --- | --- |
| Intent | `LLM_INTENT_` |
| Schema | `LLM_SCHEMA_` |
| Column pruning | `LLM_COLUMNS_` |
| SQL generation | `LLM_SQL_` |
| Validation/review | `LLM_VALIDATION_` |
| Answer explanation | `LLM_RESPONSE_` |
| Visualization (optional) | `LLM_VISUALIZATION_` |

Every prefix supports `MODEL`, `ENDPOINT`, `API_KEY`, `API_STYLE`, `MAX_TOKENS`, `TIMEOUT_SECONDS`, `STRICT_TOOLS` and `SEND_PARALLEL_TOOL_CALLS`. The orchestrator is deterministic LangGraph routing; execution is a database tool. Neither needs an LLM.

```dotenv
# Retain existing shared LLM_ENDPOINT / LLM_API_KEY / LLM_MODEL.
LLM_INTENT_MODEL=openai/gpt-oss-20b
LLM_INTENT_MAX_TOKENS=1000
LLM_SCHEMA_MAX_TOKENS=1500
LLM_COLUMNS_MAX_TOKENS=1500
LLM_SQL_MODEL=openai/gpt-oss-120b
LLM_SQL_MAX_TOKENS=4000
LLM_SQL_TIMEOUT_SECONDS=60
LLM_VALIDATION_MODEL=openai/gpt-oss-120b
LLM_VALIDATION_MAX_TOKENS=2000
LLM_RESPONSE_MODEL=openai/gpt-oss-20b
LLM_RESPONSE_MAX_TOKENS=1500
```

These are examples, not claims of current availability/pricing. Output-token caps constrain response size, not total monetary cost; input tokens also cost money. Different reviewer models can diversify review but do not prove correctness.

A role model wins over UI/CLI model selection; otherwise that selection overrides the shared default. Other omitted role settings inherit shared settings. A role endpoint requires its own API key and model, preventing accidental shared-credential disclosure. Endpoint suffixes infer API style if a role style is omitted. Each endpoint/model must pass the existing source-specific egress policy and support typed tool calls.

API, CLI and default Python workflows share these rules; custom model factories retain control. Evaluation reports include sanitized per-role settings in run identity, excluding keys. Restart the backend after environment changes; finish/cancel reviews before changing provider settings.

## Capacity and retries

| Variable | Default | Meaning |
| --- | --- | --- |
| `QUERY_WORKERS` | 1 | 1–16 concurrent segments |
| `QUERY_MAX_PENDING` | 3 | Active + queued segments; worker count–200 |
| `QUERY_MAX_PER_USER` | 3 | 1–50 unfinished jobs per user, including reviews |
| `QUERY_MAX_SQL_ATTEMPTS` | 3 | 1–10 generation/repair attempts per API question |
| `QUERY_MAX_MODEL_CALLS` | 40 | 1–200 total LLM calls per API question, including edits |

The manager also bounds unfinished jobs at 50. Reviews release workers. Queue saturation returns HTTP 429. Size concurrency against provider quotas and warehouse connections. The UI keeps one unfinished question per workspace and backs off polling after temporary failures; polling never resubmits SQL. Cancelling running work retains its capacity slot until it exits.

Malformed handoffs have at most three calls, consuming the global budget. SDK transport retries remain disabled (`max_retries=0`); authentication/quota/connection failures never auto-resubmit UI jobs. SQL repair stays within attempt/call budgets and requires fresh reviews. Edited SQL fails closed instead of being silently rewritten. Evaluation CLI retries are separately bounded.

Run **one API process**: queue ownership and cancellation handles are local. Threads support modest shared usage. Large deployments still need a durable shared queue, worker leases, per-source/provider rate limits, central cancellation routing, submission idempotency and deployment admission control. Multiple API workers are not a supported scaling shortcut.

## Editing and cancellation

- At SQL review, **Edit SQL → Validate edited SQL** checks bound values, permissions, row/PII controls, dialect, read-only rules and model review. Nothing executes. Fresh approval is required; old approval versions/fingerprints are rejected. Edits cannot expand confirmed tables. Bound values use a JSON list of name/value objects; exact decimals use a value such as {"type":"decimal","value":"123.45"}. Unsafe browser-sized numeric integers are rejected instead of rounded.
- **Stop query** cancels queued/reviewing work or signals active work. **Stop and edit question** cancels first, then restores the English question and source/domain selection. Replacement submission stays disabled until the active call exits, then follows the full review flow.
- **Cancellation requested** remains visible until the worker returns. Late results are discarded and subsequent stages stop. Completed reads and incurred charges cannot be undone. In-flight model calls may last until their timeout.

Cancellation is owner-only with session/CSRF checks. Direct SQL editing is available before execution. To replace running SQL, stop/edit the question, then edit/review the new SQL before approval. Typing a draft alone does not cancel work.

If the server restarts while an edit awaits or undergoes validation, that in-flight edit is not restorable; run the question again. Once a fresh SQL review is persisted, normal safe-review restoration applies. An old SQL proposal is never restored as approval for an unvalidated edit.

| Source | Active-read behavior |
| --- | --- |
| SQLite | Request-scoped interrupt and cancellation-aware progress checks, including protected direct reads and local processing |
| PostgreSQL / Redshift / Oracle | Calls request connection `cancel()` when exposed by the installed driver |
| BigQuery | Sends cancellation for the submitted job; timeout/error cleanup also attempts cancellation |
| SQL Server / MySQL / Snowflake and unsupported drivers | Cooperative stop and result suppression; existing query limits apply. Immediate remote termination is not claimed |

Callbacks are request-scoped and removed before connection release. Cancellation signals run outside workflow workers. Native cancellation is best effort: completion races, grants or networking can prevent it. Live remote cancellation has not been certified against customer warehouses. Unsupported drivers may need warehouse tooling. App status does not prove billing stopped.

The optional Visualization Agent runs only after a user requests a chart. Its separate budget, library selection and browser rendering are documented in [VISUALIZATIONS.md](VISUALIZATIONS.md).
