# Restore saved reviews after a server restart

Implemented on 14 November 2026, extending [non-blocking reviews](NON_BLOCKING_REVIEWS.md). Table-confirmation and final-SQL-review checkpoints now survive both graceful shutdown and an unexpected process restart. Recovery is explicit: nothing is automatically executed at startup.

## User flow

1. Reopen the application and sign in. Existing browser history references are checked against the server.
2. Eligible interrupted questions show **Restore review**. This restores saved planning progress without resubmitting the question or calling a model.
3. The server checks the current account/access stamp, source/table scope, checkpoint version, workflow budgets and original deadlines. SQL checkpoints also require an unchanged effective execution-plan fingerprint.
4. A fresh table proposal or SQL approval panel appears with a new version. Old confirmation/approval versions are rejected. Review and approve again before any business-data read.

Use **Run question again** if a review expired or its permissions, schema, plan or workflow limits changed. Restoration preserves the original review deadline and history expiry; it does not renew either lifetime. The normal review window is ten minutes. Restoration reads permitted metadata, not business rows, and does not freeze the underlying data. Newly approved SQL reads the then-current database.

## Recovery boundaries

| Last durable phase | Behavior after restart |
| --- | --- |
| Waiting for table confirmation | Restore the table proposal; require a new confirmation, then generate/review SQL. |
| Waiting for SQL approval | Restore the generated SQL without repeating model calls; require fresh SQL approval. |
| Accepted decision queued but not started | Return to the corresponding review checkpoint. A stored decision is never executable authorization. |
| Running a segment | Mark interrupted and require a new question run. Calls made inside the interrupted segment are not replayed automatically. |
| Execution may have started | Mark interrupted, disclose uncertainty, and never automatically replay the SELECT. |
| Completed or failed | Preserve existing history behavior; no continuation is available. |
| Expired, deleted, legacy or missing checkpoint | No restoration. Existing retention/ownership rules apply. |

The server durably changes a checkpoint to `running` before continuing a segment, and to `executing` before entering the execution stage. A crash between those markers and the actual work can conservatively make an attempt non-restorable even if no read occurred. This is intentional: a saved SQL approval cannot establish that a query was never executed. A read started before a crash may already have happened; this is not an exactly-once execution system.

Work before the first review checkpoint is not recoverable. In-flight model steps, response-only recovery after execution, automatic restart of queued work, distributed dispatch and multi-process recovery remain future work. Run one API process against the records database. Old snapshots without the new checkpoint format cannot acquire resumability retroactively.

## Storage and security

`query_progress` in the application records database stores a versioned JSON checkpoint and a small phase/deadline index. Snapshot and checkpoint updates commit in one SQLite transaction. Checkpoints contain plain workflow artifacts—question, intent, schema, reviewed examples, candidate SQL, routing, model-call/attempt counters, original proposal and workflow limits. Edited table selections can be restored as suggestions for renewed confirmation. No live model/database objects, result rows, internal execution tickets or accepted SQL decisions are stored as recovery authority. Public API responses never include the private graph state.

Checkpoints use the application's exact-number codec and, when `PII_ENCRYPTION_ENABLED` is enabled, its configured cipher with query-bound associated data. Checkpoints can contain sensitive questions, literals and metadata; with encryption disabled they are plaintext application records protected by server file permissions. Keep the same encryption key across restarts. Wrong keys, malformed state, incompatible formats, exhausted/invalid budgets and changed limits cannot authorize execution.

The original lifecycle deadline governs checkpoints. Deletion/expiry removes them with the query and feedback; late writes cannot resurrect unregistered, deleted or expired query IDs. Terminal outcomes clear the checkpoint. Graceful shutdown preserves safe review checkpoints. Explicit user cancellation, access invalidation and provider-policy failure clear them. Backup retention and deletion-ledger replay after backup restoration remain separate, unfinished work.

`POST /api/queries/{id}/restore-review` accepts an empty JSON object. It requires the owner, a current authenticated session and CSRF protection; administrators cannot restore another person's query. It checks permissions before decoding private progress and again after metadata revalidation. Concurrent/repeated restoration cannot activate the same checkpoint twice within the supported single-process manager. Normal version-bound table/SQL decisions still gate subsequent segments.

Restoration preserves the saved model-call and SQL-attempt counters and checks the configured limits. Safe queued checkpoints have no additional calls in flight. Running checkpoints are not replayed, so their partial work cannot silently reset the saved budget. Starting a fresh question is a separate, user-requested run with new approvals and budgets.

## Verification

Checks cover restored table/SQL workflows against real SQLite results, preserved six-call budgets, old-approval rejection, queued decisions returning to review, durable pre-read execution markers, non-replay of running/executing phases, permission/plan/limit changes, deadline checks, encrypted round trips and wrong-key denial, atomic rollback, deletion/expiry cascade, owner/CSRF checks, strict request validation and the UI restore/fresh-run actions. No live provider calls, automatic database execution or legacy-record purge was performed for this increment.

Historical verification for the restart-recovery increment: 118 backend/API tests and 20 frontend tests passed, the production frontend build passed, and the dependency check reported no broken requirements. These checks do not establish live warehouse behavior or independent security assurance.

For the latest complete regression count, see [README verification](README.md#verification).
