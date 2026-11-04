# ETL operating runbook

| Situation | Action |
|---|---|
| Upload rejected | Read extraction audit and HTTP error. Check UTF-8/format/size, source contract and key availability. Failed unparsed bytes are not kept; correct and upload again. |
| PII key unavailable | Validate the referenced external key and identity permissions. Do not disable protection or paste key material into a pipeline/chat. |
| Schema drift | Compare expected and observed paths/types/order. Review a new source-contract revision; target schema changes need a deliberate migration. |
| Quality gate failed | Inspect quarantine and batch checks. Fix data/mapping or propose a reviewed rule change. Do not weaken critical rules solely to get a green run. |
| Warning check failed | Review the reported value/threshold and owner decision; publication may have succeeded. Warning reports do not imply an alert email was delivered. |
| Append collision | Choose a new immutable key/data batch or deliberately use an upsert recipe after review. |
| Partition overwrite blocked | Check explicit partition values and keys colliding outside scope. Never widen overwrite partitions without impact review. |
| SCD history mismatch | Stop and reconcile/migrate current detail against history. Do not switch SCD2 to a different strategy to bypass the check. |
| Gold failed | Verify transaction rollback and prior published Gold. Investigate nonfinite values, limits or contract/config errors; replay retained sources with a new key after repair. |
| Interrupted run | No partially committed publication is expected. Inspect audit/current target, then replay the pinned snapshots with a new key after review. |
| Cloud execute blocked | This release exports cloud plans only. Select local execution for a local reference run or implement/configure and integration-test the selected warehouse adapter. |
| Decryption denied | Normal workspace access does not grant PII access. Use the separate operator credential through the scoped decrypt API; record and investigate anomalous access. |
| Key rotation | Use a new immutable key reference; keep prior keys for existing ciphertext, inventory dependent sources/runs/backups and re-encrypt through an approved migration. |
| Existing plaintext PII | Restrict access; inventory datasets, run reports, exports, WAL and backups. Plan migration/purge and verify storage remediation before claiming protection. |

Back up the SQLite database consistently together with referenced encryption-key recovery procedures. A database backup without required historical keys cannot replay encrypted data. Copies and exports have independent retention/access obligations. Cloud restores, deletion workflows, legal holds and disaster-recovery objectives require a separate tested operating plan.
