# Email notifications for applied changes

Notifications are recorded in a **transactional outbox** within the same SQLite transaction as each qualifying audit event. An SMTP failure does not undo a pipeline activation, lose the queued notification or prevent a later retry.

## Events

- New managed pipeline version activated after human review.
- An earlier pipeline configuration restored through approved rollback.
- An explicit schema mapping proposal approved.
- A bounded remediation applied.
- Optionally, a pipeline run publishes records (`EMAIL_NOTIFY_PUBLICATION=true`).

Drafting and previewing are not applied changes, so they do not send email. Version registration is an import, not a publication or version-change notification.

## Configure privately in .env

```dotenv
EMAIL_ENABLED=true
EMAIL_NOTIFY_PUBLICATION=false
SMTP_HOST=
SMTP_PORT=587
SMTP_TLS_MODE=starttls
SMTP_USERNAME=
SMTP_PASSWORD=
EMAIL_FROM=
EMAIL_TO=
```

Use your organization's approved SMTP service, sender identity and recipient addresses. `EMAIL_TO` accepts up to twenty comma-separated plain addresses. Set `SMTP_TLS_MODE=ssl` and its required port if your provider uses implicit TLS. TLS is mandatory; plaintext SMTP mode is not supported. Configure both username/password or neither for an explicitly approved relay. Keep credentials out of chat, source control, screenshots and API request bodies.

Delivery is **disabled by default**. Applied changes still queue durable pending records, visible in **Change review → Change email notifications**. When enabling delivery, those pending notifications may be sent to the currently configured recipients. Review that backlog before enabling. The worker reads recipients from server configuration at delivery time; recipients are never chosen by a model or taken from source data.

Restart after changing settings. A recipient and SMTP provider were requested during implementation; no real delivery can be verified until those settings exist. Tests use mocked SMTP and never send email.

## Content, retries and limits

Emails contain event type, UTC time and allowlisted pipeline/version/change/proposal/run identifiers. Full recipes, prompts, sample rows, source content and credentials are omitted. Rollback notices explicitly state that warehouse rows were not reverted.

The background sender leases one pending event, uses verified STARTTLS/SSL, and records sent status after SMTP acceptance. Transient delivery failures retry with bounded backoff, up to five attempts. Configuration, authentication and recipient failures are shown as blocked. Failed/blocked messages can be retried manually after correcting settings. SMTP exception text is sanitized before storage.

Delivery is **at-least-once**. SMTP acceptance followed by a process crash can produce a duplicate; the message uses a stable Message-ID to aid deduplication but cannot guarantee it. A partially accepted multi-recipient send can also produce duplicates when manually retried. SMTP acceptance is not a guarantee of inbox delivery; provider delivery/bounce monitoring remains external work.

The worker runs only while the application process is alive. Restart resumes pending delivery or expired leases. A durable dedicated worker, provider webhooks, delivery SLOs, named recipients/authorization and recipient-level retry tracking are production extensions. Email is an advisory notification, not proof of human approval.

## API

- `GET /api/notifications/status`: enabled/configured status and recipient count; no secrets.
- `GET /api/notifications`: latest 100 outbox records and sanitized delivery errors.
- `POST /api/notifications/{id}/retry`: requeue a failed/blocked delivery.

These endpoints use the same access control as the change-review APIs. SMTP credentials and recipient configuration cannot be modified through them.
