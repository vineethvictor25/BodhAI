# Configure different models for different agents

The application can assign different models to its six primary LLM specialists and its optional Visualization Agent. You can share one provider/key across agents, or configure separate approved providers. Set connections and budgets in `.env`; use `model_policy.json` to authorize where source data may be sent.

## 1. Choose which agents need overrides

| Agent | Role name | Model variable | Work performed |
| --- | --- | --- | --- |
| Intent | `intent` | `LLM_INTENT_MODEL` | Interpret the English question |
| Schema | `schema` | `LLM_SCHEMA_MODEL` | Select relevant permitted tables |
| Column pruning | `columns` | `LLM_COLUMNS_MODEL` | Select useful columns |
| SQL | `sql` | `LLM_SQL_MODEL` | Generate SQL |
| Validation | `validation` | `LLM_VALIDATION_MODEL` | Review SQL semantics alongside deterministic security checks |
| Response | `response` | `LLM_RESPONSE_MODEL` | Explain query results |
| Visualization (optional) | `visualization` | `LLM_VISUALIZATION_MODEL` | Recommend a chart/library after a user requests visualization |

The orchestrator uses LangGraph routing and the Execution Agent uses database tools; neither makes an LLM call. Workspace routing and approved-example retrieval are also deterministic. Different models do not change the workflow order, permissions, table confirmation or final SQL approval.

## 2. Set a shared fallback and agent overrides

Edit `.env` in this application's directory, alongside `.venv`. Keep a valid shared connection for agents without overrides and other model-backed app features. This example uses one Groq endpoint with two model IDs already covered by the bundled Groq policy example:

```dotenv
LLM_ENDPOINT=https://api.groq.com/openai/v1
LLM_API_KEY=replace-with-your-groq-key
LLM_MODEL=openai/gpt-oss-20b
LLM_API_STYLE=chat_completions
LLM_MAX_TOKENS=4000
LLM_TIMEOUT_SECONDS=90
LLM_STRICT_TOOLS=false
LLM_SEND_PARALLEL_TOOL_CALLS=true

LLM_INTENT_MODEL=openai/gpt-oss-20b
LLM_INTENT_MAX_TOKENS=1000

LLM_SCHEMA_MODEL=openai/gpt-oss-20b
LLM_SCHEMA_MAX_TOKENS=1500

LLM_COLUMNS_MODEL=openai/gpt-oss-20b
LLM_COLUMNS_MAX_TOKENS=1500

LLM_SQL_MODEL=openai/gpt-oss-120b
LLM_SQL_MAX_TOKENS=4000
LLM_SQL_TIMEOUT_SECONDS=60

LLM_VALIDATION_MODEL=openai/gpt-oss-120b
LLM_VALIDATION_MAX_TOKENS=2000

LLM_RESPONSE_MODEL=openai/gpt-oss-20b
LLM_RESPONSE_MAX_TOKENS=1500

QUERY_MAX_SQL_ATTEMPTS=3
QUERY_MAX_MODEL_CALLS=40
MODEL_POLICY_FILE=model_policy.json
```

These assignments illustrate configuration, not guaranteed model availability, quality or prices. Replace IDs with models available to your account. Output caps apply per call; input tokens and retries also contribute to cost. An agent can make more than one call. `QUERY_MAX_MODEL_CALLS` bounds total calls across API workflow segments and edits, while `QUERY_MAX_SQL_ATTEMPTS` bounds SQL generation/repair attempts. CLI workflows use `--max-model-calls` and `--max-attempts` for these global budgets.

Every agent supports the following suffixes after its prefix, such as `LLM_SQL_`:

| Suffix | Example | Meaning |
| --- | --- | --- |
| `MODEL` | `LLM_SQL_MODEL` | Exact provider model ID |
| `ENDPOINT` | `LLM_SQL_ENDPOINT` | OpenAI-compatible provider URL |
| `API_KEY` | `LLM_SQL_API_KEY` | Server-side credential |
| `API_STYLE` | `LLM_SQL_API_STYLE` | `chat_completions` or `responses` |
| `MAX_TOKENS` | `LLM_SQL_MAX_TOKENS` | Output budget, 1–100000 tokens; provider limits may be lower |
| `TIMEOUT_SECONDS` | `LLM_SQL_TIMEOUT_SECONDS` | Per-request timeout, greater than 0 and at most 600 |
| `STRICT_TOOLS` | `LLM_SQL_STRICT_TOOLS` | Whether to request strict tool schemas |
| `SEND_PARALLEL_TOOL_CALLS` | `LLM_SQL_SEND_PARALLEL_TOOL_CALLS` | `true` sends `parallel_tool_calls=false`; `false` omits that provider parameter |

Omitted role settings inherit shared settings. Defining a role endpoint requires both that role's API key and model, even if it points to the same provider. For one shared endpoint, setting only role model/token overrides is sufficient. A changed endpoint can infer API style from its suffix if the role style is omitted; setting the role API style explicitly makes the choice clear. The adapter must support the provider's typed tool-call behavior.

## 3. Approve matching policy profiles

For the single-provider example, one profile can authorize both models:

```json
{
  "version": 1,
  "sources": {
    "sample": {"classification": "internal", "require_local": false}
  },
  "profiles": [
    {
      "id": "groq-agent-models",
      "endpoint": "https://api.groq.com/openai/v1",
      "models": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
      "approved": false,
      "classifications": ["public", "internal"],
      "allow_detected_pii": false,
      "max_payload_bytes": 65536
    }
  ]
}
```

This example deliberately blocks model calls until an operator reviews the provider/data policy and sets the profile to `approved: true`. Merge it into the existing policy instead of replacing real-source classifications or local-only requirements. Do not store keys in JSON. Each agent's endpoint/model must match exactly one approved profile that permits the source classification. Two agents can share a profile; profiles are not assigned by agent name.

Policy endpoints use normalized base URLs, without `/chat/completions` or `/responses`. Full endpoint suffixes may appear in `.env`, but use the normalized URL in the policy. A trailing slash is tolerated; other URL differences and model IDs must match exactly.

To mix OpenAI, Claude and Gemini between agents, follow the complete [multiple-provider policy and environment examples](MODEL_POLICY_EXAMPLES.md). They explain compatibility endpoints, provider-specific constraints and the separate approvals required. Adding a profile does not certify a provider's tool-call compatibility.

## 4. Understand precedence and the UI selector

The effective model is selected in this order:

1. The agent's `LLM_<ROLE>_MODEL`.
2. The browser model preference or CLI `--model` fallback.
3. Shared `LLM_MODEL`, then the applicable provider alias such as `GROQ_MODEL`.

The UI model selector therefore changes only agents without an explicit role model. For example, selecting 20B in the UI will not change an SQL Agent pinned to 120B. Remove an agent's override to make it follow the fallback again; check both `.env` and process environment variables. Process variables override the same variable in the file, but a shared process variable does not override a differently named role variable.

`NLQ_ENV_FILE` can select another explicit environment file. API, CLI and the default Python workflow use role settings; a custom Python `model_factory` controls its own choices. Changing models does not automatically fall back to another provider on failure.

## 5. Restart and verify

Finish or cancel pending reviews before changing provider settings. Restart the backend so all subsequent work uses the intended configuration. With the managed UI launcher, run these commands from this application's directory:

```powershell
.\stop-ui.ps1
.\start-ui.ps1 -OpenBrowser
```

The following local check, also run from this directory, prints model/budget choices and validates policy matching for the sample source without sending provider requests or printing API keys:

```powershell
@'
from langgraph_impl.config import ModelConfig
from langgraph_impl.model_policy import ModelPolicy

policy = ModelPolicy.load()
for role in ('intent', 'schema', 'columns', 'sql', 'validation', 'response', 'visualization'):
    settings = ModelConfig.for_role(role)
    print(role, settings.model, settings.max_tokens, settings.timeout)
    matched = policy.check(settings, 'sample', {'purpose': 'configuration check'})
    print('  approved profile:', matched['profile'])
'@ | .\.venv\Scripts\python.exe -
```

This checks the default role configuration without a UI/CLI model override. It does not validate API credentials, account access or live tool-call compatibility. After it passes, try a non-sensitive sample question, confirm the tables, review the SQL, and approve execution. Never send real sensitive data merely to test a provider.

## Troubleshooting

The [application troubleshooting guide](TROUBLESHOOTING.md) also covers setup, connections, security, execution, cancellation and result/history problems.

| Symptom | What to check |
| --- | --- |
| UI model preference does not change an agent | The agent's role model takes precedence; inspect/remove that override |
| Role endpoint requires its own key/model | Supply both `LLM_<ROLE>_API_KEY` and `LLM_<ROLE>_MODEL` |
| No exactly matching approved profile | Check normalized endpoint, model spelling, approval flag and overlapping approvals |
| Shared model works but a later agent is blocked | The later agent may use a different endpoint/model that needs its own policy match |
| Source classification cannot be sent | Check the source classification/local-only rule and the matching profile's permitted classifications |
| Provider rejects a parameter or tool handoff | Check API style, strict-tool and parallel-parameter settings; compatibility layers may need provider-specific adjustments |
| Output is incomplete or repeated attempts exhaust the budget | Review token caps, request timeout, handoff validity and global call/attempt budgets |

See [query controls](QUERY_CONTROLS.md) for queue limits, cancellation and existing retry behavior. Evaluation reports retain sanitized per-agent model settings in their run identity and exclude API keys.

Visualization has a separate on-demand call budget and renderer allowlist. See [result visualizations](VISUALIZATIONS.md). Primary query evaluations cover the six query roles; visualization checks are separate.
