# Operations and production-readiness guide

## Runtime configuration

| Variable | Default | Purpose |
|---|---|---|
| PROVIDER | local | `local` deterministic recipe or `openai` live adapter |
| OPENAI_API_KEY / OPENAI_MODEL | unset | Backend-only live provider configuration |
| DATA_DIR | data | Database directory; relative to process working directory |
| APP_API_TOKEN | unset | Shared bearer token; required for non-loopback API clients and containers |
| API_ALLOWED_HOSTS | empty | Exact public HTTPS hostname allowlist for source snapshots |
| MAX_UPLOAD_BYTES | 31457280 | Source/document byte limit (30 MiB); HTTP envelope gets a 64KB allowance |

Restart after changing configuration. The startup script always changes into the project directory. Run one application process. The HTML/JS/CSS shell and `/health` are public; data APIs and `/api/schema` use the access policy. Unauthenticated APIs accept loopback clients only. When a bearer token is set, all API requests need it, including local ones. Cross-origin mutation requests are rejected. This is a development authentication model; put OIDC and authorization in front of an enterprise deployment.

## Data retention and backups

Data stays in `data/warehouse.db` with its WAL/SHM files. The project intentionally has no delete endpoint and no automatic purge. Source rows, extracted attributes and rejected raw values can be sensitive. Store this directory on an appropriately protected volume and establish a retention policy before using real data. Hashes identify content but are not encryption or an external tamper-evidence mechanism.

Use SQLite's online backup API, not a copy of just the main database while writes are active:

```powershell
.\.venv\Scripts\python.exe -c "import sqlite3; a=sqlite3.connect('data/warehouse.db'); b=sqlite3.connect('data/backup.db'); a.backup(b); b.close(); a.close()"
```

To restore, stop the server, preserve the current database directory, and restore a tested backup into a separate DATA_DIR. Start against that directory and inspect run counts, contracts, quarantine and warehouse records before switching users. The application audit trail does not provide an automatic business-data rollback; restore/replay procedures must account for later upserts.

## Troubleshooting

**Browser cannot connect:** the current default is **8765**, not 8000. Run `.\start.ps1` from your own PowerShell and open `http://127.0.0.1:8765/health`. If the console says the port is occupied, use `.\start.ps1 -Port 9000`. Do not stop unknown processes on another port. During implementation, both 8000 and 8765 reported successful application startup but local connection attempts from the automation environment timed out. Direct in-process HTTP tests passed. The v1.1 preview launched outside that restricted execution context was reachable at port 8766 and verified in the browser. A startup log alone does not verify browser reachability.

**403 in a container:** set APP_API_TOKEN and enter it using Configure access token. Docker connections typically do not appear as loopback to the server.

**401:** enter the configured token. The UI does not persist credentials across a page reload.

**Quality failed:** inspect required mappings, per-row issues, unit formats and reject ratio. Do not bypass the gate merely to make a run green. If price currency formatting is the cause, review a generated repair proposal. All other cases require a reviewed source/recipe change.

**Contract failed:** the same target already has different column names/types or a different primary key. Use a new logical target for a reviewed migration, or implement an explicit migration process; automatic destructive DDL is not supported.

**Interrupted run:** the server restarted before finalization. Review the persisted recipe and source snapshots and submit with a new key. The original key returns the interrupted run for traceability.

**Live AI failure:** check the selected model, account access and key privately. Errors disclose HTTP status, not the provider body or key. Availability errors follow the configured route and call budgets. Mapping can request one same-route correction for malformed output or omitted append sources. Temporary throttling can leave a blocked partial draft visible. Use **Preview mappings** for offline name and alias matching. Never paste credentials in an error-log diagnosis field.

**Image/scanned PDF:** local mode cannot perform OCR. Enable a vision-capable provider for PNG/JPEG; export scanned PDF pages to images first. Encrypted PDFs and PDFs above 30 pages are rejected.

## Before multi-user production use

- Add individual authentication, role-based API authorization and named patch approvers.
- Use TLS termination, a reverse proxy with byte/time/concurrency limits and restricted ingress/egress.
- Move HTTP execution to a durable job queue, add leases and graceful drain, and implement migration/version compatibility.
- Introduce data classification, PII redaction, model cost budgets and organization-approved model policies.
- Back up and restore-test data; implement retention, warehouse rollback and immutable external audit logs.
- Add metrics and alerts for run age, failure rate, rejection rate, provider latency, retries, database contention and publication lag.
- Benchmark realistic datasets and define SLOs before replacing SQLite or increasing source limits.
- Test cloud source/destination credentials, pagination, retries, rate limits and target transaction semantics.

These are outstanding production integrations, not features claimed by this local reference implementation.
