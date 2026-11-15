# Review waits that release the workflow worker

Implemented on 14 November 2026, following [final SQL approval](SQL_APPROVAL.md). Normal reviews require no server restart: confirm tables or approve SQL on the current page to continue. The subsequent [restart-recovery increment](RESTART_RECOVERY.md) adds durable review checkpoints with fresh approval.

## Behavior

The production browser workflow runs in segments. It stops after schema selection for table confirmation, and after SQL generation/metadata validation for final SQL approval. Each stop returns the worker to the queue. Other users' questions can then progress through the same single workflow worker, without increasing simultaneous model pipelines.

When a user responds, the server consumes the matching proposal version once and queues the next segment. Before continuing, it checks the account/access stamp, history lifetime and review deadline. SQL approval also requires the expected execution-plan fingerprint; the newly built plan must match it before any data read. Repairs still require new table and SQL review. Model-call budgets and SQL-attempt counters carry across every pause.

The workflow constructs a fresh graph/model connection for each segment. Between segments it retains plain planning artifacts in a private, bounded process-local continuation map: question, intent, schema, candidate, routing and counters. It does not retain a suspended worker, live database connection, model client or reusable execution capability there. Model clients created for a segment are closed on return. The execution capability is freshly issued and consumed inside the approved execution segment after revalidation.

A single lightweight deadline monitor expires idle proposals without occupying the workflow worker. Cancellation and administrator access changes discard waiting continuations immediately. Deletion removes them and the saved snapshot under the existing lifecycle checks; late work cannot recreate deleted history. Existing table/column/PII, metadata-only planning, provider policy and read-only controls remain enforced.

## Capacity and user experience

Worker/queue and per-user limits are now [configurable](QUERY_CONTROLS.md). The default deployment still has one workflow worker, at most three queued/running segments and at most 50 unfinished jobs, including waiting reviews. Waiting jobs no longer consume the three runnable slots. A full runnable queue returns a retryable response without consuming the pending review. An accepted decision must reach the worker before its original review deadline; waiting in the queue does not extend approval.

The browser continues polling existing job IDs. Review panels remain available while other users query. This is improved use of existing capacity, not measured support for a high-volume shared service. Long-running model requests and queries still serialize. Per-user quotas, multi-worker dispatch, load testing and distributed execution remain future work. Run one API process; multiple API processes sharing the records database are not supported by this process-local job manager.

## Restart and recovery boundary

A restart is **not** needed to continue a review. Safe waiting and not-yet-started queued segments now have [durable review checkpoints](RESTART_RECOVERY.md). After restart, the owner can explicitly restore an unexpired review with current permissions and a new proposal version. Restoring does not run SQL. Graceful shutdown preserves these checkpoints; deletion, cancellation and expiry clear them.

Old proposal versions cannot restart execution on a new process. No pending SQL is automatically executed after restart. Running segments and uncertain executions are marked interrupted and require a new question run. A read started before a crash may have occurred; this does not promise exactly-once execution across crashes. In-flight stage recovery and distributed dispatch remain separate work. A saved approval is never a reusable authorization token.

## Verification

Tests use one worker and show a second real count query finishing while the first remains at review. Coverage also includes SQL-review worker release, six model calls across three segments, connection/client cleanup, bounded budgets, attempt limits across repair, version replay, queue-full decisions, idle expiry, immediate cancellation, access revocation, deletion, changed-plan rejection, interrupted snapshot handling and shutdown cleanup. Existing authenticated API, PII, provider-policy and read-only tests remain in the regression suite. No live provider calls or remote-warehouse performance claims were introduced.
