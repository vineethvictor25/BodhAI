# Column-level PII protection

## Implemented controls

Only selected sensitive columns are encrypted with AES-256-GCM. Each cell uses a fresh random 96-bit nonce and authenticated column-path context. Other fields remain ordinary values. The protected value contains a format version and a non-secret key reference, never the key. Decryption verifies integrity and column context.

Source contracts can declare `pii_columns` as flattened paths and `pii_key_ref` as an external key name. Conservative name/value heuristics also flag common email/phone/DOB/identifier fields and email/SSN-shaped values. Arrays containing flagged values are treated as a protected column. These heuristics cannot reliably identify every person name, address or contextual identifier; owner classification remains mandatory.

PII ingress needs a configured key. Missing/unavailable keys reject ingestion before raw values are stored. Bronze retains protected JSON rows for replay, not unprotected original PII file bytes. The original file checksum and protected snapshot checksum are recorded separately. Non-PII original bytes still remain unchanged. Failed parsing/classification attempts retain audit metadata but not unparsed bytes.

The server decrypts source cells in memory for reviewed mapping, cleaning and joins. Sensitive source paths require an explicit target protection policy, even if renamed to a generic target column. Silver/Gold/exports keep the resulting protected representation. Diagnostics suppress clear values for protected columns; quarantine retains the protected source snapshot. Encrypted cells remain ciphertext in ordinary previews and downloads. Protected-field statistics withhold value extrema/cardinality. Model mapping samples are withheld for flagged/encrypted fields; an outbound guard blocks known clear-text email/SSN patterns and protected PII values before any provider/fallback request.

## Choose a policy per target column

| Policy | Behavior | Appropriate use |
|---|---|---|
| `encrypt` | Randomized authenticated, reversible column encryption | Restricted retrieval of contact/details |
| `mask` | Replace downstream nonnull values with `[REDACTED]` | Consumers do not need the original value |
| `hmac_sha256` | Stable keyed pseudonymous token | Consistent joins, business identity and grouping |
| `none` | Ordinary field | Explicitly nonsensitive data only |

Masking affects downstream output; source replay values remain encrypted. HMAC tokens are pseudonymous, not anonymous, and remain governed data. Unkeyed `sha256` is not the PII tokenization policy. Encryption and HMAC must use separate keys. Randomized ciphertext cannot be a business key, Gold grouping field, deduplication order or SCD tracked attribute; use a stable keyed companion field. Encrypted output uses string type.

In Pipeline studio, read the current columns under **PII · protect individual columns**, select classification/action/key reference, and apply. The equivalent executable column configuration is:

```json
{"name":"email","dtype":"string","classification":"pii","protection":"encrypt","key_ref":"PII_ENCRYPTION_KEY","transforms":["trim","lower"]}
```

For a keyed identifier, choose `protection: hmac_sha256` with a separate reference such as `PII_HMAC_KEY`. Normalize deliberately before tokenization so the join meaning is consistent across pipelines.

## Key providers

Local testing supports externally injected environment variables. The referenced variable must contain standard base64 for exactly 32 random bytes. Do not commit values, paste them in chat, or place them in pipeline YAML. `.env.example` contains empty development placeholders; this release does not generate/store a production encryption key for you.

Azure supports a real optional Key Vault resolver:

1. Install `requirements.azure.txt` in the virtual environment.
2. Set `PII_KEY_PROVIDER=azure_key_vault` and the administrator-controlled `PII_KEY_VAULT_URL=https://your-vault.vault.azure.net`.
3. Grant the runtime managed identity least-privilege secret-read access. The source/column `key_ref` is the Key Vault secret name; store the base64 key in that secret.
4. Validate access and representative protected data before production. Azure connectivity/identity has not been live-tested here.

Use immutable versioned secret names when rotating keys: retain old referenced keys until old cells are re-encrypted under a reviewed migration. Replacing a key's value without retaining its old version makes existing ciphertext unreadable. Do not log resolver errors, key values or decrypted content. No key-rotation scheduler or bulk rekey job is implemented.

## Separately authorized decryption

Decryption is disabled by default. An operator can configure a separate `PII_DECRYPT_TOKEN` of at least 32 characters outside the pipeline. `POST /api/privacy/decrypt` requires normal application authorization plus `X-PII-Access-Token`. It accepts one encrypted Silver column, one run and at most 20 explicit run row numbers (in input order); rejected or filtered rows are not available. It returns only matching selected values and records a metadata-only access audit. API responses use no-store caching. For a run with encrypted columns, **Quality & runs → Inspect → View encrypted values** provides a step-up token input and decrypts only explicitly selected Silver rows; the UI clears the token after each request and lets the operator clear displayed values.

This is a development step-up boundary, not named-user RBAC. Production needs OIDC roles, scoped column entitlements, approval/purpose-of-access, break-glass monitoring, short-lived credentials and separate key-vault privileges. Any process identity permitted to resolve decryption keys can decrypt data; isolate workers and key access accordingly.

## Important remaining boundaries

- Existing plaintext datasets, runs, old exports, SQLite WAL pages and backups are not retroactively sanitized. Inspect, migrate and purge according to an approved policy before real PII onboarding.
- Bronze protection does not encrypt the whole SQLite database or filenames/control metadata. Use encrypted disks/storage and TLS in production as additional controls.
- Keys, anonymous free text, uploaded knowledge and images require broader DLP/NER/classification review. Pattern checks are defense in depth, not proof that a model request is PII-free.
- Retention enforcement, individual deletion across dependent versions/backups, legal holds, per-tenant key isolation, field-level warehouse access and formal privacy compliance are not implemented by these controls.
- Gold aggregates can still expose small groups; suppression/minimum-group policies need consumer-specific design.

Architecture guidance: [OWASP cryptographic storage](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html), [OWASP logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html), and [Azure Key Vault Python client](https://learn.microsoft.com/en-us/python/api/overview/azure/keyvault-secrets-readme).
