# Conversational changes, human review and version rollback — v1.3

## Use the workflow

1. Restart the server to load v1.3 and refresh the browser. Open **Change review**.
2. Select an existing completed run and register a named pipeline. Version 1 pins that run's exact recipe, source snapshot hashes and per-source mappings. This does not rerun or publish data.
3. Describe a change. **Live AI** proposes typed operations using the existing configured model. **Offline examples** is an explicitly selected, bounded exact grammar, useful for demonstrating the full flow without API credits. There is no silent fallback.
4. Review the unified diff, operation list and conversation history. Refine a draft by leaving **Continue the current draft conversation** selected. Each response is saved as a separate immutable proposal with a parent link.
5. Choose **Preview against pinned data**. Baseline and candidate run through the same pure transform/quality function. Compare accepted/rejected counts, recovered/newly rejected rows, changed records, identity changes and quality checks. Preview writes only its review report/audit event; it creates no execution run, silver rows, quarantine rows, target contract or warehouse write.
6. A human must acknowledge the exact diff and preview before **Approve & activate**. New rejections and record-identity changes require their own acknowledgements. Failed previews cannot be activated. The server enforces these requirements independently of the UI.
7. Activation creates a new immutable version and moves the active pointer. It does not execute. Separately authorize **Run active version** if publication is intended. Existing quality gates and target-contract checks still apply.
8. To restore an earlier recipe, select a prior version, provide a reason and explicitly approve configuration rollback. Rollback creates a new version pointing to the earlier definition, retaining the complete history.

### Offline example

Run the existing sample in Overview, import its quality-failed run and select **Offline examples**:

```text
Parse price as currency and retain quality rules
```

The preview should recover the two currency-formatted prices. With the sample 20% reject threshold, it has five valid rows and one rejected invalid product. Approving the version publishes nothing. Running the newly active version can publish the five valid rows. Restoring version 1 leaves all already-published warehouse rows unchanged.

Other exact offline instructions:

```text
Set category transforms to trim, upper
Set max reject ratio to 20%
Map TARGET_COLUMN to source.path in dataset DATASET_ID
```

The offline parser matches the whole instruction and rejects unsupported suffixes. Live mode supports additional permitted scalar edits through a typed schema: transform sequences, per-source mappings, defaults, stricter numeric bounds, required fields, allowed-value sets and quality/confidence thresholds. It cannot rename/type-change target columns, change the target or primary key, add sources, join records, run arbitrary code or perform a warehouse migration.

## Guardrails and evaluations that fit

- Typed operations and Pydantic validation prevent the proposal from supplying executable Python/SQL/shell code.
- The server preserves target/source identities and verifies literal source paths, target coverage, unique source use and source hashes.
- Conversational edits cannot raise the reject ratio, lower mapping confidence, make required fields optional, relax an existing numeric bound or broaden an enumeration. Deliberate policy exceptions need a separate administrative process; a model prompt cannot grant one.
- Current-version checks protect generation, preview and activation from stale edits. An already active newer version requires a fresh/rebased proposal.
- Activation is bound to the exact previewed candidate hash. Explicit request keys make proposal retries idempotent. Versioned execution uses the exact saved definition and pinned mappings; passing a different recipe to `/api/runs` with a managed version reference is rejected.
- Known injection/credential patterns are rejected. This remains a conservative heuristic, not general prompt-injection protection or PII redaction.
- Preview includes deterministic replay evaluations. Tests cover policy weakening, fabricated paths, invalid source reuse, stale activation, unapproved activation, no-publish behavior, idempotency, version history and rollback semantics. These checks are more directly useful than an uncalibrated LLM judging its own edits.

This is a **bounded, human-in-the-loop workflow**, not an autonomous agent. LLM authority ends at drafting a patch. LangGraph, agent tool loops, Redis and vector infrastructure are not needed for this fixed path. Reconsider orchestration only if real requirements introduce long-running multi-step planning, durable background work, branching tool use or external approvals. Human review is persisted in the current database; named SSO reviewers, role separation, notifications to external approval systems and durable distributed execution are still production work.

## Rollback and operational boundaries

Rollback restores **configuration only**. It does not delete new warehouse keys, reverse past upserts, restore prior row values or cancel an in-flight run. Warehouse rollback requires before-images/versioned tables, reconciliation and a separately approved data operation. Identity changes therefore carry an explicit warning.

Runs authorized before an activation/rollback may finish against their pinned definition. The current single-process architecture has no distributed drain/cancellation service. Run idempotency keys prevent duplicate same-request publication; they are not a source-freshness ordering mechanism.

Version-managed runs intentionally reject ad hoc recipe changes using their version reference. Use the conversational workflow rather than applying the old free-standing currency repair to a managed run. Older unversioned runs continue to support the existing bounded remediation feature.

## API

| Method | Path | Action |
|---|---|---|
| POST | /api/pipelines | Register a name and completed run_id as v1 |
| GET | /api/pipelines | List managed pipelines |
| GET | /api/pipelines/{id} | Active definition, versions and recent proposals |
| GET | /api/pipelines/{id}/versions/{revision} | Inspect a historical definition |
| POST | /api/pipelines/{id}/changes | instruction, expected_revision, mode, request_key, optional parent_change_id/use_knowledge |
| GET | /api/changes/{id} | Inspect persisted proposal, diff and preview |
| POST | /api/changes/{id}/preview | Evaluate pinned baseline/candidate without publication |
| POST | /api/changes/{id}/activate | approved=true, expected_revision and any required risk acknowledgements |
| POST | /api/pipelines/{id}/run | expected_revision and idempotency_key; executes the exact active version |
| POST | /api/pipelines/{id}/rollback | approved=true, expected_revision, target_revision, reason |

All routes use the existing authorization model. A bearer token does not identify an individual approver; audit records state that limitation. JSON versions are immutable through application APIs, not tamper-proof against a database administrator. Existing production-readiness blockers remain.
