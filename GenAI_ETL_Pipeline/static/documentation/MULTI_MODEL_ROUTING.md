# Multiple models, providers and controlled fallback — v1.4

`PROVIDER` originally selected an offline product recipe or a single OpenAI adapter. It now accepts:

| Value | Behavior |
|---|---|
| `local` | Existing deterministic demo features; not a locally hosted LLM. |
| `openai` | Single OpenAI Responses route using existing OPENAI_* settings. |
| `anthropic` | Single Claude Messages route using ANTHROPIC_* settings. |
| `gemini` | Gemini through Google's documented OpenAI-compatible endpoint using GEMINI_* settings. |
| `groq` | Groq Chat Completions using GROQ_MODEL and GROQ_API_KEY. |
| `deepseek` | DeepSeek Chat Completions using DEEPSEEK_MODEL and DEEPSEEK_API_KEY. |
| `openai_compatible` | A configured compatible service using LLM_BASE_URL, LLM_MODEL and LLM_API_KEY. |
| `router` | Named models, manual primary selection and ordered availability fallback from `config/llm_routes.yaml`. |

This changes model access, not execution authority. All outputs still pass the application's Pydantic, source/policy, preview and human-review controls. Fallback cannot approve or publish a change.

## Choose any supported model in the active .env

The active configuration file is `C:\GenAI_ETL_Pipeline\.env`, not `.env.example`. Its multi-provider entries preserve existing credentials. `PROVIDER` identifies the API adapter; `LLM_MODEL` is the exact model ID and overrides the corresponding vendor-specific model setting in single-provider mode. Router mode uses each named route's model setting instead.

```dotenv
PROVIDER=groq
LLM_MODEL=your-account-model-id
GROQ_API_KEY=your-private-key
```

Gemini, DeepSeek and Claude use `PROVIDER=gemini`, `deepseek` and `anthropic` respectively with their vendor key. A populated vendor-specific API key takes precedence; `LLM_API_KEY` is the fallback when that key is absent. No model list is hardcoded: the selected model must be available to your account and support the required structured output.

Other OpenAI-compatible services can use `PROVIDER=openai_compatible`, `LLM_BASE_URL` (API root), `LLM_MODEL` and `LLM_API_KEY`. Set `LLM_STRUCTURED_MODE=json_object` if the service lacks JSON-schema mode; output remains validated locally. Optional `LLM_VISION` declares image capability. Local compatible servers additionally require `LLM_ALLOW_HTTP_LOOPBACK=true`; set `LLM_REQUIRE_API_KEY=false` only when that local service intentionally has no key. Arbitrary proprietary API formats still need an adapter. Restart after editing `.env`.

## Start with two OpenAI models

Keep your existing key and model settings. Add these privately to `.env`:

```dotenv
PROVIDER=router
LLM_CONFIG_FILE=config/llm_routes.yaml
OPENAI_FALLBACK_MODEL=your-other-available-openai-model
```

The supplied route file selects `openai_primary` and then `openai_backup`. The primary resolves OPENAI_MODEL; the backup resolves OPENAI_FALLBACK_MODEL. Both refer to OPENAI_API_KEY. A missing backup model is skipped and reported, not invented. A same-account backup may not solve an account-wide quota problem.

Use model IDs actually available to your account with the required structured-output/image capabilities. No unverified model ID is made a default. Restart after changing `.env`; the process does not reload that file automatically.

## Add another provider

Configure its model and key in `.env`, then explicitly select it for fallback:

```dotenv
GEMINI_MODEL=your-available-gemini-model
GEMINI_API_KEY=your-private-key
ANTHROPIC_MODEL=your-available-claude-model
ANTHROPIC_API_KEY=your-private-key

GROQ_MODEL=your-available-groq-model
GROQ_API_KEY=your-private-key
DEEPSEEK_MODEL=your-available-deepseek-model
DEEPSEEK_API_KEY=your-private-key

LLM_FALLBACK_ROUTES=gemini_backup,groq_backup,deepseek_backup
LLM_ALLOW_CROSS_PROVIDER_FALLBACK=true
```

These are configuration examples, not real credentials. Do not put key values in the YAML file or chat. Cross-provider/cross-endpoint fallback is disabled by default because it transmits the same request, source profiles, optional references or image to another service. Select only providers/endpoints approved for that data.

For a manual switch rather than failover:

```dotenv
PROVIDER=router
LLM_PRIMARY_ROUTE=gemini_backup
LLM_FALLBACK_ROUTES=
```

An explicitly empty fallback list disables fallback. To keep cross-provider backups after changing the primary, enable the cross-provider flag and select a valid ordered list. Duplicate/missing names, disabled primaries and unauthorized cross-provider chains are rejected before any model call.

The YAML is read per request; editing its primary/order takes effect on subsequent requests. Environment-variable changes require restarting the process. A request uses the route configuration captured for that call. There is no unauthenticated API for changing routes or secrets.

## Gemini → Groq → DeepSeek example

Supply GEMINI_MODEL/GEMINI_API_KEY, GROQ_MODEL/GROQ_API_KEY and DEEPSEEK_MODEL/DEEPSEEK_API_KEY privately in `.env`. Then set:

```dotenv
PROVIDER=router
LLM_PRIMARY_ROUTE=gemini_backup
LLM_FALLBACK_ROUTES=groq_backup,deepseek_backup
LLM_ALLOW_CROSS_PROVIDER_FALLBACK=true
```

For one service without fallback, use `PROVIDER=groq`, `PROVIDER=deepseek` or `PROVIDER=gemini` with its model/key pair. Restart after changing `.env`.

Groq and DeepSeek supplied routes use `json_object` mode with application-side schema validation. Groq JSON-schema support depends on the selected model; explicitly select `structured_mode: json_schema` only for a verified compatible model. DeepSeek JSON mode can return blank content; the application rejects it as invalid output. Groq and DeepSeek routes default to text-only. Image extraction needs an explicitly configured, verified vision-capable route; unsupported routes are skipped.

## Route file

`config/llm_routes.yaml` includes enabled route definitions for OpenAI primary/backup, Gemini, Groq, DeepSeek and Claude. Defining a route does not select it. Only `primary` and `fallbacks` are considered.

```yaml
version: 1
primary: openai_primary
fallbacks: [openai_backup]
allow_cross_provider_fallback: false
fallback_on: [transport, timeout, rate_limit, server_error, model_unavailable, quota]
max_attempts_per_route: 2
max_calls: 6
request_timeout_seconds: 45
total_timeout_seconds: 120
routes:
  - name: openai_primary
    provider: openai
    model_env: OPENAI_MODEL
    api_key_env: OPENAI_API_KEY
    vision: true
  - name: openai_backup
    provider: openai
    model_env: OPENAI_FALLBACK_MODEL
    api_key_env: OPENAI_API_KEY
    vision: false
```

Use either a literal `model` or `model_env`, never both. `api_key_env` is an environment-variable name, not a secret value. Set `vision` to match the actual selected model; it is an administrator declaration, not automatic model-capability discovery. A text-only route is skipped for image requests, and the router never silently discards the image.

For a compatible server, supply `provider: openai_compatible` and an approved `base_url` ending at its API root, such as `/v1`. The adapter appends `/chat/completions`. HTTPS is required except explicitly allowed HTTP on localhost/127.0.0.1/::1. Embedded credentials, query strings and fragments are rejected; redirects and ambient proxies are disabled. Endpoint configuration is trusted administrator configuration, never model-generated input. Workload identity, Azure-specific authentication and arbitrary provider SDK options are not implemented.

Compatible routes default to `structured_mode: json_schema`. If a particular service lacks that wire format, explicitly choose `json_object`: the full schema is included in the prompt and the returned JSON is still validated server-side. JSON-object mode does not establish model-side schema adherence. OpenAI Responses and native Claude routes use their JSON-schema formats; provider-specific schema subsets and model compatibility must be tested. A schema/API rejection is not hidden by switching providers.

## Recovery policy

| Result | Behavior |
|---|---|
| Transport/timeout, HTTP 408, rate-limit 429, 5xx | Bounded retries on the route, then the next selected route if allowed by fallback_on. |
| Recognized quota/billing-limit 429 | No repeated attempt on that route; move to an explicitly configured eligible backup. |
| Recognized model-unavailable 404 | Move to the next selected route. A generic endpoint 404 is treated as configuration error. |
| Missing model/key or unsupported image capability | Skip that route with a sanitized reason. |
| HTTP 400/401/403 or other request/configuration failures | Stop and report; do not hide invalid configuration or authentication. |
| Refusal, content filtering or truncated response | Stop on the current route. |
| Invalid JSON/schema or omitted append source in discovery | Mapping can request one same-route correction within call/time budgets. If rate-limited, a partial discovery result stays available for review when present; approval remains blocked. |
| All routes fail or call/time budget is reached | Return an error. Do not fabricate a result or silently switch to the offline compiler. |

The attempt ceiling applies across routes, so it can be exhausted before the last route. Timeout budgets are checked between attempts and bound HTTP phase timeouts; they are not a hard wall-clock cancellation/SLO guarantee. The router has no background job queue, persistent circuit breaker or cross-request health cache. Each new call starts at its configured primary.

Legacy single-service settings retain bounded attempts without a fallback chain. The old offline examples remain explicitly selected features and are never a recovery route for live AI failure.

## Visibility and auditability

Successful responses include actual provider/model/route, whether fallback was used, usage returned by the provider and a sanitized attempt history. Semantic mapping/extraction/change proposals retain that metadata through their existing persistence paths. Generated plans return it to the caller. Optional LLM knowledge answers also include it.

`GET /api/config` exposes `llm_routing`: selected routes, model names, enabled/configured/vision flags and policy limits. It excludes credentials, authorization headers and raw provider error bodies. Model failures return an `llm_error` category and sanitized attempt history. These endpoints use the existing access policy.

## Validation and remaining limits

The v1.4 suite covers multiple model order, primary retries, call budgets, rate/quota/timeout/server/model-unavailable failover, cross-provider and cross-endpoint permission, refusal/validation stop behavior, manual route override, image filtering, unconfigured-route skipping, protocol payloads and configuration redaction.

Automated protocol tests use mocked provider responses. Live Groq discovery and mapping were verified with synthetic datasets in JSON-schema mode. The earlier OpenAI verification returned HTTP 429. Verify each selected route with non-sensitive representative inputs and the actual model/account before production. Provider terms, retention, region, schema support, cost and quotas may differ. Existing production identity, queue, scaling and operations gaps remain.

Official protocol references consulted:

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Claude structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)

- [Groq OpenAI compatibility](https://console.groq.com/docs/openai)
- [Groq structured outputs](https://console.groq.com/docs/structured-outputs)
- [DeepSeek JSON output](https://api-docs.deepseek.com/guides/json_mode/)
