# Natural-language schema mapping

Pipeline Studio can now infer semantic **source-to-target** mappings from natural-language instructions and real source profiles using the configured model route. **Generate AI mappings** maps to an existing contract. **Infer schema, keys & mappings** can propose a new target contract and equivalent fields across sources. The prior alias/fuzzy mapper remains available as the offline **Preview mappings** operation.

## Use it

1. Restart the server after changing `.env` or application code, then refresh the browser.
2. Stage `samples/semantic_products.csv` in **Data sources**. It uses supplier-specific names such as `merch_ref`, `display_caption`, `package_span`, and `selling_value`.
3. Open **Pipeline studio** and review your target schema. For the included sample, use `currency_number` for the price transform and `length_m` for the measurement transform. All other existing quality rules still apply.
4. Select the source dataset. Under **Auto-generate schema mappings with natural language**, describe the business correspondence, for example:

   > The merchandise reference identifies the product. The display caption is its name. The package span contains its length with units. Selling value is its price, settlement currency is its currency, and department label is its category.

5. Click **Generate AI mappings**. Review the source path, explanation, evidence and confidence for each target column. Edit a source selection if needed. Low-confidence matches are left unselected; the suggested path is shown separately. Resolve every required field.
6. Click **Approve reviewed mappings**, then **Run reviewed pipeline**. The engine reads the persisted approved decisions; it does not silently revert to fuzzy inference.

Multiple sources are analyzed jointly. For append, each source receives bindings to the common target. For join, review the key relationship and row impact, then stage the combined source and approve its final bindings. The **Data model** diagram traces the selected target field and reflects mapping edits. Configure supported transforms and aggregations in the target recipe. See [Multi-source mapping](MULTI_SOURCE_MAPPING.md).

## What the model receives

The request includes your instructions, the target specification, source name, row count, flattened paths, observed type counts, missing/null counts, and up to **three unique sample values per field, truncated to 80 characters each**. It does not send whole source files. Profiles use up to 1,000 sampled rows plus available schema inventory. Full-snapshot key and join validation runs locally. Each request supports at most 100 flattened fields per source and at most five selected sources. Instructions and model explanations are stored in the local proposal audit data.

The profile withholds samples for detected or declared sensitive fields and protected tokens. Classify sources before sending requests to an external model because automated detection is heuristic. See [Column PII controls](PII_HANDLING.md) and [model routing](MULTI_MODEL_ROUTING.md).

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
POST /api/mappings/discover
  {"instructions":"...","dataset_ids":["...","..."],"combination_mode":"auto"}
  -> inferred schema, field equivalences, key evidence and combination review

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

Configure the selected provider and model on the backend. Mapping may request one correction on the same route within call and time budgets. If the correction is rate-limited, discovery can keep a partial draft visible for review. When no usable result exists, the app reports the rate limit and shows the provider wait time when available. **Preview mappings** remains available offline. See [model routing](MULTI_MODEL_ROUTING.md).
