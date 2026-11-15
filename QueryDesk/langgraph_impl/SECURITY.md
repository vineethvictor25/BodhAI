# Authentication, inherited permissions and local PII protection

Remote connectors default to verified TLS 1.2+ through `WAREHOUSE_TLS12_ENABLED=true`. Operators can set `false` to permit plaintext development SQL connections; Snowflake and BigQuery remain on verified HTTPS. The effective transport policy participates in SQL approval and is disclosed before execution. See [TLS_CONNECTIONS.md](TLS_CONNECTIONS.md) for per-engine behavior, CA configuration and pending live-server verification.

## First use and accounts

Run `setup.ps1` first if this application's `.env` does not exist. Generate a random setup token of 32–256 characters with `.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"`, place it in the ignored `.env` as `AUTH_SETUP_TOKEN=...`, then start the app and enter that token in the setup screen along with the administrator account and password. Do not expose an empty instance before setup. The server compares the token in constant time and performs first-account creation in a transaction so concurrent attempts cannot create two administrators. No default token or password is bundled. After setup, the token can be removed from `.env`.

The administrator can query every table allowed by each configured data-source profile, including PII. Application read-only SQL controls and the underlying database account grants remain enforced; administrator access does not grant SQL writes or bypass operating-system/database permissions.

After setup, other people can choose Create an account and select exactly one source/domain. The server creates a member account with no direct PII permission and membership in Business Users. The public onboarding catalog contains source/domain labels needed for that choice, not table rows or schema. New accounts cannot submit roles, groups, extra grants or PII flags.

Authentication uses salted scrypt password hashes (`N=32768,r=8,p=3`), opaque eight-hour sessions whose authentication tokens are stored only as hashes, HttpOnly/SameSite=Strict cookies, origin checks, CSRF tokens and bounded authentication attempts. Passwords, cookies and CSRF values are never saved to browser storage. These scrypt parameters follow the [OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

## Direct grants and groups

Open Access and security as administrator to manage members, direct domain grants, enabled state, PII permission and group membership. Changes to a member revoke that member's sessions. Group/domain-policy changes invalidate affected request snapshots and waiting confirmations; current permissions are checked again at query stages and database calls. Members must rerun queries after a permission change; old results cannot be used to bypass a revocation.

Standard groups:

| Group | Initial policy |
| --- | --- |
| Business Users | No extra domains or PII permission; assigned at onboarding |
| PII Readers | Allows decrypted PII within the member's assigned domains |
| PII Restricted | Explicitly denies decrypted PII |

Administrators can create more groups. Effective domains are the union of direct and group grants. For members, an explicit group PII denial wins over a direct or inherited allow. Otherwise a direct PII grant or any group allow enables PII viewing. Administrators retain full source/domain/PII access; row-policy bypass is separately explicit in configuration and defaults to true. Group grants added to Business Users intentionally apply to new and existing members of that group.

**Configurable application row security is implemented.** [ROW_SECURITY.md](ROW_SECURITY.md) documents the separate policy file, activation, per-source mappings, inherited group/account attributes, composite parent relationships and limitations. It is disabled until the operator configures/enables it. Account rules use server-issued IDs, not self-selected usernames. Missing attributes and unmapped tables deny rows; group/direct restrictions intersect. Policies filter before joins/aggregations and are included in final approval fingerprints. JSON changes invalidate waiting work and old recovery access stamps. The admin API reports capability and enabled state separately, identifies application enforcement, and explicitly reports live warehouse verification as pending. Raw row-filter SQL is not accepted through group-editing or query APIs.

## Domains: relevance versus access

A domain remains a relevance hint for table selection. The user's assigned domains form a separate hard access boundary: only their union of tables is queryable. The LLM can propose tables outside the chosen hint only when another assigned domain permits them. Domains alone do not isolate rows. When enabled, configured row policies further restrict those tables, independently of the chosen hint and PII permission.

Only administrators can change domain definitions or request AI domain drafts, because changing a domain's table list now changes access. Source-level table permissions cannot be broadened through domains. Metadata, examples, table confirmation, validation, execution, query history and feedback use authenticated scope. A member cannot read or mutate another person's query by guessing its ID. Administrators can inspect live saved queries. Snapshots without lifecycle metadata are quarantined for every HTTP user until explicit operator adoption or purge.

Browser history now stores per-account job IDs only, not questions or result rows. Old browser-storage entries from versions before authentication are not imported; operators using an existing browser profile may clear those legacy entries. The application security database and CLI remain trusted server/operator resources, protected through OS file permissions. CLI commands do not impersonate browser members.

## PII classification and permissions

The sample classifies `customers.name`, `customers.phone`, `customers.mobile`, `customers.email` and `customer_profiles.email` as PII. All 12 sample customers have synthetic phone/mobile values from the fictional `+1-202-555-01xx` range and email addresses under `customers.example.test`. Existing profile-email NULL cases are retained for evaluation.

Members without PII permission do not receive those columns in schema metadata, and those values are excluded from their queryable data before SQL executes. This covers wildcard projection, aliases, expressions and predicates; it is not a display-only mask. SQL questions requiring protected fields must use non-sensitive IDs or obtain permission. For authorized reads, decryption happens on the server locally. Non-NULL result cells are conservatively replaced with opaque request-local tokens before the response LLM; approved answer tokens are restored locally for the authorized response. SQL must compute arithmetic/comparisons, since opaque values cannot support model-side calculation. Questions, SQL parameters, reviewed examples and business metadata are still sent to the configured model and are not automatically anonymized.

Review the PII map for each external source. An explicit empty mapping means the administrator reviewed that source and classified no columns as PII. A member without PII permission is denied access to an unmapped external source. This is configured classification, not automatic PII discovery.

## Encryption configuration

The server `.env` contains:

```dotenv
PII_ENCRYPTION_ENABLED=false
PII_CONFIG_FILE=encryption.json
AUTH_COOKIE_SECURE=false
```

PII authorization remains active when encryption is disabled. `AUTH_COOKIE_SECURE=false` is for the current loopback HTTP preview. A reviewed HTTPS deployment must use secure cookies and appropriate trusted-host/origin/proxy configuration; the provided scripts continue to bind to loopback.

`encryption.json` starts from `encryption.example.json`:

```json
{
  "provider": "aes-gcm",
  "key_file": ".secrets/pii.key",
  "sources": {
    "sample": {
      "customers": ["name", "phone", "mobile", "email"],
      "customer_profiles": ["email"]
    }
  }
}
```

The implemented local provider is AES-256-GCM with a fresh random 96-bit nonce per value. Associated data binds fields to source, table, column and primary key. The implementation uses the [cryptography AEAD API](https://cryptography.io/en/latest/hazmat/primitives/aead/). The envelope is versioned; unknown providers or wrong/missing keys fail closed. Additional mechanisms require a reviewed cipher adapter; arbitrary module paths are not loaded from config. Keys never appear in prompts, graph state, browser configuration or API responses.

When enabled, application query snapshots and feedback comments are encrypted on write, and existing snapshots/comments are sealed at startup. Authentication account names, domain definitions, access policies, audit metadata and externally stored files/logs are not encrypted by this feature. Full-disk encryption and appropriate filesystem permissions are separate host controls.

## Explicit local column migration

The flag does **not** silently rewrite business databases. Local SQLite column encryption is an offline operator action. The original source remains plaintext until this migration is run. From the repository root:

```powershell
# Stop the app before a data migration.
.\langgraph_impl\stop-ui.ps1

# This creates the configured key once and never prints or replaces it.
.\langgraph_impl\run.ps1 --init-pii-key

# Review encryption.json, then explicitly encrypt mapped local sample columns.
.\langgraph_impl\run.ps1 --pii-migrate encrypt --apply --backup data/sales-before-pii.enc
```

Set `PII_ENCRYPTION_ENABLED=true` in `.env` after initializing the key, then restart. The key path must have OS permissions limited to the service/operator account; the initial file uses owner permissions where supported, and Windows deployments must review inherited ACLs. Back up the key separately in protected storage. Losing the key loses access to encrypted data. Key rotation/keyrings and cloud key-management providers are not implemented; do not replace an active key.

Migration requires existing SQLite DELETE journal mode, an offline/exclusive database, stable primary keys, and TEXT PII fields outside primary/foreign/unique keys. It creates an authenticated encrypted backup before mutation and authenticates every existing encrypted value. Do not rename source IDs, tables, columns or row primary keys while values are encrypted without a deliberate migration. SQLite secure-delete and vacuum are used; this does not erase old external backups, crash remnants or storage-device history.

To reverse column encryption with the same key, stop the app and run `--pii-migrate decrypt --apply --backup data/sales-before-decrypt.enc`. Choose a fresh backup path each time. Encrypted backups contain a source-bound AES-GCM JSON envelope with a base64 SQLite snapshot; recovery requires the same key and original source ID. Turning off the flag alone does not decrypt existing columns or records, and their key must remain available.

SQLite uses [direct protected reads](DIRECT_READS.md) for supported queries that do not reference classified PII, avoiding source-row copying. Rewritten projections/predicates, read-only transactions, native authorization, schema checks and limits remain enforced. Classified-PII queries retain bounded local handling (100,000 rows / 20 MB) for decryption semantics. All registered remote engines use source-side protected projections and configured row filters. See [WAREHOUSE_CONNECTORS.md](WAREHOUSE_CONNECTORS.md) for transaction, driver, cloud IAM and TLS boundaries; SQL Server/Snowflake/BigQuery require restricted source grants rather than an adapter read-only transaction. External ciphertext decryption/native at-rest encryption is not provided by the SQLite migration. Live warehouse checks remain pending. The [connector contract](CONNECTOR_CONTRACT.md) defines required controls for future adapters; unregistered implementations fail closed.

## Verification

[Same-instance SQL Server mappings](SQLSERVER_CROSS_DATABASE.md) preserve explicit app table/domain grants, classified-column removal, configured row predicates and final approval of expanded three-part SQL. Source login permissions do not expand the app allowlist. Direct/native SQL Server grant and certificate checks remain pending until a live deployment is supplied.

[Bounded encrypted PII handling](BOUNDED_PII.md) retains the local row/plaintext-copy ceilings and adds configurable source-byte, envelope/cell, decryption-count and preparation-time limits. Limits are bound to SQL approval. Oversized ciphertext is rejected before decryption/parsing; bound failures close resources and end the attempt without partial results or automatic SQL repair. SQLite local temporary work stays in memory. Payload accounting does not bound total process memory; cooperative deadlines cannot preempt OS I/O or atomic cryptographic operations. Other engines need tested adapters before advertising this capability.

The Phase A changes add durable query-history expiry/deletion, lossless exact numbers and mandatory model data policy; its pattern scanner is not a complete sensitive-data classifier. [Final SQL approval](SQL_APPROVAL.md), [non-blocking reviews](NON_BLOCKING_REVIEWS.md) and [restart recovery](RESTART_RECOVERY.md) preserve permissions, budgets and expiry across reviews. [Configurable row security](ROW_SECURITY.md) now adds per-source predicates/parent inheritance and policy-change revocation. SQLite, encrypted PII, tenant collisions and connected-source paths are tested; live PostgreSQL/MySQL and independent security verification remain pending. In-flight/distributed recovery and report/audit/backup retention remain unfinished.

Checks cover unauthenticated denial, atomic setup, one-domain onboarding, admin-only changes, CSRF/logout, rate limits, ownership, revocation, inherited PII denial, actual graph execution, local encryption round trips, row-bound authentication, hidden PII predicates/aliases, source dialect projections, encrypted records and synthetic contacts. This is an initial security implementation, not an independent penetration test or security certification.
