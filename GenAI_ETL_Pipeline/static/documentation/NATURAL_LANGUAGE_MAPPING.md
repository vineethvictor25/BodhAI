# Natural-language schema mapping — v1.1

Pipeline Studio can now infer semantic **source-to-target** mappings from natural-language instructions and real source profiles using the configured OpenAI model. This is separate from target schema generation. The prior alias/fuzzy mapper remains available as the offline **Preview mappings** operation.

## Use it

1. Restart the server after changing `.env` or application code, then refresh the browser.
2. Stage `samples/semantic_products.csv` in **Data sources**. It uses supplier-specific names such as `merch_ref`, `display_caption`, `package_span`, and `selling_value`.
3. Open **Pipeline studio** and review your target schema. For the included sample, use `currency_number` for the price transform and `length_m` for the measurement transform. All other existing quality rules still apply.
4. Select the source dataset. Under **Auto-generate schema mappings with natural language**, describe the business correspondence, for example:

   > The merchandise reference identifies the product. The display caption is its name. The package span contains its length with units. Selling value is its price, settlement currency is its currency, and department label is its category.

5. Click **Generate AI mappings**. Review the source path, explanation, evidence and confidence for each target column. Edit a source selection if needed. Low-confidence matches are left unselected; the suggested path is shown separately. Resolve every required field.
6. Click **Approve reviewed mappings**, then **Run reviewed pipeline**. The engine reads the persisted approved decisions; it does not silently revert to fuzzy inference.

For multiple source files, each file gets independent mappings to the same target contract. For example, `merch_ref` in CSV and `product.code` in JSON can both populate `product_id`. A source field may be used only once within each dataset. There is no join discovery, aggregation, expression generation, or source-specific unit conversion in this feature. Configure supported column transforms separately before running.

## What the model receives

The request includes your instructions, the target specification, source name, row count, flattened paths, observed type counts, missing/null counts, and up to **three unique sample values per field, truncated to 80 characters each**. It does not send whole source files. Profiles inspect the complete bounded dataset; only the bounded profile goes to the model. Each request supports at most 100 flattened fields per source and at most five selected sources. Instructions and model explanations are stored in the local proposal audit data.

Sample values can still contain sensitive information. Review data classification before using real sources. The adapter uses `store=false`; that flag is not a claim of zero data retention by the provider. See the [Responses structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

## Validation and approval

- Structured output is validated with Pydantic. Every target must appear exactly once.
- Hallucinated source paths, duplicate source usage and contradictions of explicit overrides are rejected before a proposal is saved.
- Confidence values are the model's self-assessment, not calibrated probabilities. Below-threshold suggestions require an explicit source selection.
- Approval explicitly decides every target field, including null for an optional unmapped field. Required fields cannot remain unmapped.
- Approved decisions are immutable and persisted in `mapping_proposals`; revise them by generating a new proposal.
- Execution verifies approval, exact dataset IDs and content hashes, target identity, column names/types and primary key. Changes to that target schema require a fresh proposal. Current required-field rules are also rechecked.
- The browser invalidates approval when the edited specification, selected sources or mapping instructions change. Execution never calls the model again, so retries use the same approved decisions.
- Mapping proposal IDs participate in the run idempotency fingerprint and appear in run request snapshots, mapping reports and audit events.
- Mapping approvals do not relax any validation, quality thresholds or publication transactions. A transformation-only repair can reuse the approval when the mapping's target identity is unchanged.

The shared development token records approval actions but does not establish individual reviewer identity. Enterprise deployment still needs named approvers and authorization.

## API

```text
POST /api/mappings/generate
  {"instructions":"...","spec":{...},"dataset_ids":["..."]}
  -> persisted proposal with per-source mappings and model metadata

GET /api/mappings/{proposal_id}
  -> proposal, status and recorded approval

POST /api/mappings/{proposal_id}/approve
  {"approved":true,"decisions":{"DATASET_ID":{"product_id":"merch_ref", ...}}}

POST /api/runs
  {"spec":{...},"dataset_ids":["..."],"idempotency_key":"...",
   "approved_mapping_id":"PROPOSAL_ID"}
```

If `approved_mapping_id` is omitted, the existing deterministic mapper is used. If it is provided but invalid, unapproved or stale, execution is rejected. There is no silent fallback from live semantic mapping to offline name matching.

## Live API troubleshooting

`PROVIDER=openai`, `OPENAI_API_KEY` and `OPENAI_MODEL` must be configured on the backend. An HTTP 429 may indicate a temporary rate limit or a billing/quota issue. The adapter reports known `insufficient_quota`/billing-limit errors explicitly and does not retry them. Resolve the API project's available credits and budget before retrying. No key is sent to the frontend or included in project packages.
