# Evaluation, table review and feedback

## Reliable evaluations

The development suite contains 30 cases. The held-out suite contains 37 authored cases covering ambiguity, missing values, time boundaries, partial payments/receipts, independent child aggregates, bridge/composite joins, zero versus no rows, unsafe requests, payment/refund reconciliation and outstanding balances. Both use frozen expected results. Held-out cases are never loaded by `KnowledgeBase` or sent to the SQL generator. Approved examples remain available during held-out evaluation, as in normal operation. Some SQL patterns overlap; this is not a production or fully novel-task benchmark. Reference execution verifies the answered cases; behavioral cases still need live evaluation and human review.

From the repository root:

```powershell
# Verify fixture answers without model calls. Choose a new report name for a new run.
.\langgraph_impl\run.ps1 --evaluate --suite heldout --reference-only --report outputs/heldout-references.json

# Start small: two independent generations of one held-out question.
.\langgraph_impl\run.ps1 --evaluate --suite heldout --case heldout_missing_email --repeat 2 --pause-seconds 2 --report outputs/heldout-smoke.json

# Resume with the same case, repeat count, model, dataset and retry settings.
.\langgraph_impl\run.ps1 --evaluate --suite heldout --case heldout_missing_email --repeat 2 --pause-seconds 2 --report outputs/heldout-smoke.json --resume
```

`--model` selects a model override. `--eval-retries` defaults to 2 (maximum 5). Only rate limits, connection errors and selected temporary HTTP errors retry. Every retry starts a fresh read-only graph, so even a failure during explanation can re-read data. SQL/semantic failures are not retried by the evaluator. A trial preserves its retry journal across interruption; a call interrupted before returning can be repeated. Execution approval capabilities are never persisted.

`Retry-After` seconds/date and `retry-after-ms` are supported. If the provider requests a delay above `--retry-delay-cap` (default and maximum 60 seconds), the trial records a failure without retrying earlier than requested. Without guidance, waits are 1, 2, 4… seconds. Separate pacing between trials uses `--pause-seconds`. Neither retries nor pacing increase provider quota.

Each completed trial is saved before progress is printed. Retry attempts are saved before sleeping. JSON replacement is atomic and flushed to disk; an OS file lock prevents concurrent writes to a report. Resume checks the fixture hash, cases, repetitions, permitted tables, result limit, model configuration, context assets and retry settings. It skips completed trials, including failures. Use a new report to re-run failures. Do not alter a saved report to force a resume.

Reports contain provider endpoint/model/settings (never API keys), per-attempt outcomes, final SQL/results, expected rows, timing, pass rates, stable-pass cases and distinct-output consistency. `result_correct` compares executed rows with the frozen expected rows, including NULLs, duplicates, order requirements and numeric tolerance; this determines answer correctness. `sql_reference_match` separately compares the parsed/normalized SQL and bound parameters with the authored reference. It is a strict structural diagnostic, not proof of semantic equivalence, and does not fail an otherwise result-correct run solely because an equivalent SQL formulation differs. Clarification/refusal cases have no applicable SQL match. Older version-2 reports are backfilled with these additive fields when resumed; the existing result-comparison fingerprint remains unchanged. Provider outages remain failures in the overall pass rate; infrastructure counts distinguish them from incorrect answers. Timing for a resumed unfinished trial measures the resumed segment; individual attempt timings preserve earlier work. Reports contain business data, so keep `outputs` private.

`tools/build_heldout.py` is an explicit authoring tool, not part of evaluation/startup. Do not regenerate expectations just to pass a failing run. References and behavior rubrics currently have authored fixture checks; independent business review remains pending.

## Table review and feedback

The UI understands the question, routes workspaces and proposes tables. Users review the reason and editable permitted-table list, then confirm or cancel. SQL generation, pruning and metadata/model review follow table confirmation. A separate [final SQL approval](SQL_APPROVAL.md) is then required before source compilation or business-data reads. Empty/out-of-scope edits, duplicate decisions and stale versions are rejected. Repair requires fresh confirmation. The server expires unanswered proposals after ten minutes. [Non-blocking reviews](NON_BLOCKING_REVIEWS.md) release the worker while waiting; safe checkpoints can be [explicitly restored](RESTART_RECOVERY.md) after restart with fresh approval, subject to their original deadlines.

Completed/failed query snapshots and review decisions are saved in `langgraph_impl/outputs/querydesk.sqlite3` with owner-scoped expiry/deletion. History expires 24 hours after submission by default; raw result rows are omitted from durable snapshots by default. Feedback and checkpoints are removed with their query. See [PHASE_A.md](PHASE_A.md) for settings and backup limitations. Feedback includes query ID, rating, answer/SQL/tables category, optional correction, timestamp and pending review status. The query snapshot supplies the question, confirmed tables, SQL and answer. Feedback works for persisted completed questions after restart. Running stages and uncertain executions are not replayed; eligible review checkpoints require explicit restoration and renewed approval.

Read pending reviews locally:

```powershell
.\langgraph_impl\run.ps1 --review-feedback
```

An operator can inspect the linked query snapshot in the local SQLite records. Review corrections against the question, metric definitions, permissions and actual results. If approved, deliberately curate a new example or evaluation case in source control and run reference/regression checks. Nothing is automatically promoted; raw feedback never enters model prompts.

## Verification

The small live held-out smoke evaluation on 13 November 2026 ran `heldout_missing_email` twice: one run returned the correct rows; the second exhausted its bounded rate-limit retry. The saved report records 1/2 passes and a provider failure separately, rather than treating the outage as a correct result. A resume read the completed report without reissuing model calls. This is a smoke check, not accuracy evidence for the full benchmark. Run `--reference-only` to recheck all 37 held-out SQL references offline; expected non-answer behavior still requires broader live review and independent business review.

## Optional vector retrieval

A future vector index can supplement lexical retrieval for business definitions, table/column descriptions and reviewed SQL examples. Keep permission/workspace filtering before retrieval, combine semantic and keyword scores, and revalidate retrieved SQL against the current schema. Keep evaluation answers and unreviewed feedback out of this index. Transactional data remains in SQL; the small fixture does not require vector storage.
