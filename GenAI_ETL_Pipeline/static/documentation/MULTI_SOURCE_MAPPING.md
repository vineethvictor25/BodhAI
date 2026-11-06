# Multi-source discovery, mapping and merge

## Choose the starting point

| Pipeline Studio action | Input | Output |
|---|---|---|
| **Infer schema, keys & mappings** | Two to five selected snapshots and optional business instructions | AI-suggested target schema, source field equivalences, candidate keys, combination plan and mappings |
| **Generate AI mappings** | An existing target contract and one to five selected snapshots | AI-suggested bindings to the existing target. With several sources, the model also recommends how to combine them |
| **Preview mappings** | An existing target and selected snapshots | Local name and alias matches with an interactive data model |

## Review discovery

1. Select the source snapshots. Choose **Auto**, **Append** or **Join** in the combination selector.
2. Add business instructions when the intended grain or field meanings are known. For example, “One target row per SKU. Use the inventory SKU as the target primary key. Left join inventory to the product catalog on SKU. Design Number and Style refer to the same design.”
3. Click **Infer schema, keys & mappings**. Review **AI-detected equivalent columns** for the suggested shared meaning and reasoning. Equivalent fields can appear even if the model leaves the join unresolved. An equivalent field can repeat, so it still needs separate key validation.
4. Review the target grain, proposed primary key, source key candidates and combination. Select a target field in **Data model** to trace the chosen source fields. The diagram marks the target primary key and shows join type, key paths and cardinality when available. It follows edits in the mapping table.
5. When a validated draft is available, choose **Use inferred target draft**. For a join, select **Create reviewed combined source & generate its mappings**. This stages a new source with `left.*` and `right.*` paths, retains its parents, and requests final mappings for the combined source.
6. Review and approve the final mappings. Run the pipeline separately. For inputs within the transformation-preview limit, use **Preview Silver & Gold** before running.

## Join key versus target primary key

- **Join key.** Links rows across two sources. Supported joins require unique non-null values on the right lookup side. Left key values may repeat, which gives a many-to-one relationship. Matching is exact and type-aware. Review matched and unmatched counts, unused right rows, and rows dropped by an inner join.
- **Target primary key.** Identifies each target row. Discovery requires a single non-sensitive column with a high-confidence binding. For append it must bind every source and be unique across the combined snapshots. For join it must bind the left source and be unique and nonblank across the entire left snapshot. For example, a lookup SKU may be unique while inventory SKU repeats by location. That join can be valid while SKU fails as the target primary key.
- **Source key candidates.** Proposed primary candidates receive full-snapshot counts for duplicates and invalid values. Foreign-key roles are hypotheses. Composite keys and automatic surrogate-key inference are outside this discovery workflow. If the model proposes no key or validation fails, the draft remains unavailable for approval. Review the grain and name a business key in the instructions.
- **Unresolved relationships.** A model recommendation of “none” skips join preview. It does not establish that the data has no usable join. Give the model the intended roles and fields and retry, or use the explicit merge workflow.

## Execution and limits

- **Append mapped rows** aligns different headers to the same grain. Per-source mappings run before the normalized records enter one batch. Manual raw append in Medallion ETL requires matching top-level headers.
- **Left join** keeps unmatched left rows. **Inner join** keeps matched rows. One join stage covers two snapshots. A right source may participate through its key without contributing a target column.
- Merge and merge preview stream batches through a disk-backed SQLite index. They have no fixed total-row ceiling. Disk and runtime govern capacity. The separate Medallion transformation preview is capped at 25,000 input rows. File ingestion retains its format-specific limits.
- The model receives bounded column profiles with detected/declared sensitive samples withheld. Approved reference excerpts are optional. The server validates source paths and key evidence locally. The review exposes counts and schema without exposing join-key values.
- An incomplete append can trigger one same-route retry within model budgets. If a source remains unmapped, the review names it and blocks approval. A rate-limited correction can leave an incomplete draft visible for review.
- Join preview leaves source, run and warehouse records unchanged. Staging a join writes a new source snapshot. Mapping approval and execution are separate authorized actions.

See [API](API.md) for discovery, merge preview and approval, [model routing](MULTI_MODEL_ROUTING.md) for provider configuration, and [mapping architecture](diagrams/architecture-mapping.svg) for the complete flow.
