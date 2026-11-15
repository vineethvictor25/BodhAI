# Multiple model-provider profiles

For policy rejection messages and provider failures, see the [troubleshooting guide](TROUBLESHOOTING.md#agent-models-and-provider-policy).

For the full workflow of assigning models to agents, including shared defaults, budgets, UI precedence and verification, see [Configure different models for different agents](AGENT_MODEL_CONFIGURATION.md).

[model_policy.example.json](model_policy.example.json) is a valid, copyable policy containing four profiles: Groq, OpenAI, Anthropic Claude and Google Gemini. The active `model_policy.json` is not changed by this example. All example profiles have `approved: false`; no provider gains approval merely by appearing in the file.

## Configure a profile

1. Merge the profiles you need into your active policy, or copy the example to a separate policy file and set `MODEL_POLICY_FILE` to that path. Preserve classifications and local-only restrictions for every real source.
2. Replace model IDs with the exact models available to your account. The IDs in the sample are examples, not a recommendation or availability guarantee.
3. Review provider data handling, source classifications, payload limits and PII requirements; set `approved: true` only for reviewed profiles. Each endpoint/model must match exactly one approved profile. Do not approve overlapping profiles for the same endpoint/model.
4. Configure each agent's endpoint, model and key in `.env`. No API keys belong in the policy JSON. Restart the backend after changing environment settings.

The policy uses the normalized base URL, without `/chat/completions` or `/responses`. It accepts a trailing slash, but otherwise the endpoint and model must match exactly. Profile IDs are descriptive names; they are not selected by an agent variable. The runtime matches the endpoint/model configured for that agent.

Example overrides using three different providers:

```dotenv
# Retain your existing shared configuration as the fallback.
LLM_INTENT_ENDPOINT=https://api.openai.com/v1
LLM_INTENT_API_KEY=replace-with-openai-key
LLM_INTENT_MODEL=gpt-4.1-mini
LLM_INTENT_API_STYLE=chat_completions

LLM_SQL_ENDPOINT=https://api.anthropic.com/v1
LLM_SQL_API_KEY=replace-with-workspace-scoped-claude-key
LLM_SQL_MODEL=claude-opus-5-5
LLM_SQL_API_STYLE=chat_completions
LLM_SQL_STRICT_TOOLS=false
LLM_SQL_SEND_PARALLEL_TOOL_CALLS=false

LLM_RESPONSE_ENDPOINT=https://generativelanguage.googleapis.com/v1beta/openai
LLM_RESPONSE_API_KEY=replace-with-gemini-key
LLM_RESPONSE_MODEL=gemini-4-hydrogen
LLM_RESPONSE_API_STYLE=chat_completions
```

Use the corresponding identical model IDs in the policy. See [per-agent settings and budgets](QUERY_CONTROLS.md) for token/time limits and precedence rules. Other agents continue using shared settings unless overridden.

## Compatibility and verification

This application currently uses `ChatOpenAI` and typed tool calls. The Claude and Gemini profiles target their providers' OpenAI compatibility endpoints, rather than native Messages/Gemini API adapters. Adding a policy profile authorizes a matching destination; it does not add an adapter or certify end-to-end compatibility.

Anthropic documents compatibility limitations, including ignored strict function-call schemas and workspace-header requirements for keys not scoped to a workspace. Use a workspace-scoped Claude key with this application's current adapter; configurable provider-specific workspace headers are not implemented. Local handoff validation still applies. Native Claude structured-output integration would require a separate adapter.

The sample JSON is checked against the local policy loader and matching rules without provider calls. Paid/live tool-call compatibility with these example providers is not tested by that check. Test each chosen provider with approved non-sensitive sample data before using it for a real source.

Official references used for the example endpoints/models:

- [Official OpenAI documentation: GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
- [Anthropic OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)
- [Google Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
