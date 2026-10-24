# Production readiness

What must change before NexTrace receives **real operational data** on a
reachable endpoint. Each statement below was checked against the code on
`feature/runtime-telemetry` (2026-10-24). AWS-side facts come from earlier
deployment work and were **not re-checked**, because this review made no
AWS API calls. They are marked _(AWS, not re-verified)_.

## Current state

| Area                 | State                                                                                                                                                                                                                                                     |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Transport            | The ALB listener is HTTP only; no domain or certificate _(AWS, not re-verified)_. The ingest key (sent by producers and typed into the UI) and all investigation data therefore cross the internet in clear text.                                         |
| Writes               | `POST /events` and `POST /investigations/{id}/analysis` require `X-NexTrace-Ingest-Key` (constant-time comparison; 401 if wrong, 503 if unconfigured). **One shared key covers both.** GitHub webhooks use a separate HMAC secret.                        |
| Reads                | **Unauthenticated:** `GET /events`, `/events/{id}`, `/investigations/{id}`, `/investigations/{id}/analyses` and `/analyses/{run_id}`. The last one returns the stored AI context snapshot (event messages plus allowlisted metadata).                     |
| Abuse limits         | Body caps (64 KiB for `/events`, 1 MiB elsewhere) and field limits. **No rate limiting**, so anyone holding the key can spend AI quota as fast as requests complete.                                                                                      |
| Secrets              | Database URL, ingest key, webhook secret and AI key are injected from Secrets Manager _(AWS, not re-verified)_; none are in the image or the repository. There is no documented rotation procedure.                                                       |
| Database             | RDS PostgreSQL with `sslmode=require` and a backend-only security group _(AWS, not re-verified)_. Migrations run on every container start (`docker-entrypoint.sh`).                                                                                       |
| Logging              | Structured JSON with request IDs. Event messages and keys are never logged, and clients only ever receive a generic 500 message. The ingest key appeared 0 times in all logs during local validation. Server-side exception logs do include stack traces. |
| AI data              | Only allowlisted scalar metadata leaves the system, but **event messages are sent unfiltered**. The configured provider's retention and training terms have not been reviewed.                                                                            |
| Backups and recovery | Not configured or verified in this repository.                                                                                                                                                                                                            |
| Deployment checks    | `/health` (liveness) and `/ready` (database) exist. No automated post-deploy smoke test.                                                                                                                                                                  |

## Blockers: required before real data

1. **HTTPS.** Get a domain and an ACM certificate, add a 443 listener, and
   redirect 80 to 443. Without this, every other control below can be read
   or replayed from the wire.
2. **Authenticate reads.** Investigations and analysis snapshots reveal
   operational detail (services, versions, failure messages). Options:
   - Put the ALB behind an identity provider (ALB OIDC/Cognito
     authentication), which needs no application change.
   - Add a read key or an operator session to every `GET` route.
3. **Separate and rotate keys.** Split the event-ingest key from the
   AI-analysis key so a producer can't spend AI quota. Document rotation
   (Secrets Manager rotation plus a task restart) and rotate anything that
   has crossed plain HTTP.
4. **Rate-limit the AI endpoint**, per key or client, and cap AI spend at
   the provider.
5. **Approve AI data handling.** Review the provider's retention and
   training terms. Decide whether event messages may leave the system;
   if not, filter messages the same way metadata is filtered.
6. **Database backups.** Enable and test RDS automated backups and a
   point-in-time restore before relying on stored events or analysis
   history.

## Optional improvements

- Run migrations as a one-off task instead of on every container start.
  Two tasks starting together could otherwise race.
- An automated post-deploy smoke test: `/ready`, an authenticated
  `POST /events` round trip into a dedicated environment, and
  `GET /investigations/{id}`.
- Share error-spike counters across tasks, or aggregate in CloudWatch, if
  more than one task runs.
- Store evidence instead of re-deriving it, so historical investigations
  can be audited even after the rules change.
- Security headers (CSP, HSTS once HTTPS exists) in the frontend nginx
  configuration.
- An audit trail that records which key wrote each event.

## Not a concern today

- **CORS:** the API sets no CORS headers. The UI calls it same-origin
  through the ALB or the dev proxy, so cross-origin browser calls are
  refused by default.
- **Demo isolation:** the demo marker is server-controlled (`POST /events`
  rejects it) and correlation never mixes demo with genuine events.
