# Configurable warehouse TLS

Remote database transport is configured in the operator's `.env`:

```dotenv
WAREHOUSE_TLS12_ENABLED=true
```

`true` is the default: SQL connections require encryption, certificate/hostname verification and TLS 1.2 or newer. SQL Server's selected TDS driver uses TLS 1.2 specifically. Snowflake and BigQuery use verified HTTPS. SQLite is a local file and has no network TLS connection.

For development:

```dotenv
WAREHOUSE_TLS12_ENABLED=false
```

As requested, `false` permits unencrypted SQL connections where the engine supports them. It also removes the application's explicit minimum-version constraint where configurable. It does not force managed cloud services to accept plaintext, and does not override an explicit encrypted URL configuration. Snowflake and BigQuery always keep verified HTTPS; their drivers/services retain TLS requirements. Use this setting only for development. Restart the backend after changes. The setting is server configuration, not a permission a model or browser user can grant.

## Behavior by engine

| Engine | Enabled (`true`) | Development (`false`) |
| --- | --- | --- |
| PostgreSQL | `sslmode=verify-full`, minimum `TLSv1.2` (or explicit `TLSv1.3`) | Defaults to `sslmode=disable`; explicit `sslmode=verify-full` preserves verified TLS |
| MySQL | Verified SSLContext, minimum TLS 1.2 | Plaintext by default; configured CA/client-certificate options retain verified TLS using Python defaults |
| SQL Server/Azure SQL | Python TDS driver, TLS 1.2, peer/hostname checks, full-channel encryption | No CA by default permits plaintext; an explicit `ssl_ca` retains TLS 1.2 |
| Oracle | Verified TCPS with TLS 1.2 minimum; explicit TCPS DSN prevents dialect fallback to TCP | Defaults to TCP; explicit `protocol=tcps` retains verified TLS |
| Amazon Redshift | Verified TLS with libpq minimum TLS 1.2 | Same mode selection as PostgreSQL; the AWS cluster may independently require TLS |
| Snowflake | Verified HTTPS with explicit `min_tls_version=tls12` | Verified HTTPS with driver/service defaults; plaintext is unsupported |
| BigQuery | Verified HTTPS adapter with TLS 1.2 minimum, including credential refresh HTTPS | Verified HTTPS with Python/service defaults; plaintext warehouse emulators remain blocked |

No connector offers a certificate-verification bypass for encrypted connections. PostgreSQL/Redshift development modes accept `disable` or `verify-full`, not partially verified modes such as `prefer`/`require`. MySQL verification-disable options, Oracle TCP when enforcement is enabled, and unregistered URL options are rejected before connecting. Cloud URL options cannot select HTTP or skip verification.

## Certificates and examples

Default trust uses the driver's/system CA store; SQL Server uses the bundled certifi CA store unless a CA file is supplied. For private/on-premise certificates, configure the trusted issuing CA, not a verification bypass:

```dotenv
POSTGRES_URL=postgresql://reader:URL_ENCODED_PASSWORD@db.example.test/reporting?sslrootcert=C:/secure/db-ca.pem
MYSQL_URL=mysql://reader:URL_ENCODED_PASSWORD@db.example.test/reporting?ssl_ca=C:/secure/db-ca.pem
SQLSERVER_URL=mssql://reader:URL_ENCODED_PASSWORD@db.example.test/reporting?ssl_ca=C:/secure/db-ca.pem
ORACLE_URL=oracle://reader:URL_ENCODED_PASSWORD@db.example.test:1522/?service_name=REPORTING&protocol=tcps&ssl_ca=C:/secure/db-ca.pem
REDSHIFT_URL=redshift://reader:URL_ENCODED_PASSWORD@cluster.region.redshift.amazonaws.com:5439/reporting?sslmode=verify-full&sslrootcert=C:/secure/aws-ca.pem
```

Database hostnames must match their certificates. Install correct CA files and configure server TLS/listeners and network access separately. The app does not edit operating-system TLS policy, install certificates or provision cloud resources. There is no Oracle TCP fallback with enforcement enabled. A direct non-TLS PostgreSQL/MySQL development server requires `false`; an explicit `sslmode=disable` is rejected under `true`.

The GCP Cloud SQL Auth Proxy loopback example uses a plaintext local hop and a separately secured proxy-to-cloud hop. That local profile needs development mode, or replacement by a directly reachable database endpoint with trusted TLS and a matching certificate. The application does not create or secure a proxy automatically.

## Approval and validation

The selected transport policy is included in adapter capabilities and the approval fingerprint. The SQL review panel says whether encrypted connections are required or unencrypted development connections are permitted. A transport-policy change requires fresh review; it cannot silently reuse a previous approval. The panel describes permitted connection policy, not a measured negotiated protocol for a live connection.

Local tests verify TLS 1.2/1.3 handshakes, rejection of old-protocol peers, untrusted certificates and incorrect hostnames, per-driver settings, HTTP/verification-bypass rejection, development branches, environment parsing/forwarding and approval fingerprints. All driver/TLS packages are included in `requirements.txt` and the complete `requirements-lock.txt`; `requirements-warehouses-lock.txt` remains a connector-specific record. Actual remote-server negotiation, certificate chains, cloud identities and cancellation behavior still need deployment-specific verification; no live warehouse credentials were supplied.

References: [PostgreSQL TLS connection parameters](https://www.postgresql.org/docs/18/libpq-connect.html), [python-oracledb connection options](https://python-oracledb.readthedocs.io/en/latest/api_manual/connection.html), [Python TDS driver](https://github.com/denisenkom/pytds), [Snowflake driver TLS support](https://docs.snowflake.com/en/developer-guide/drivers). See [WAREHOUSE_CONNECTORS.md](WAREHOUSE_CONNECTORS.md) for source configuration and cloud limits.
