# Public Service Connector
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

[Simplified Chinese](PUBLIC_CONNECTOR.zh-CN.md) | **English**

YiQiao implements Public Service Connector Protocol `1.0` as a generic OAuth
boundary for installed public clients. A registered client uses Device
Authorization Grant with PKCE S256, receives project-bound opaque Bearer
tokens, and can call only the advertised memory resources. Existing project
API keys remain a separate authentication mechanism.

The service never selects behavior by product name or `client_id`. Adding a
client is an application-record operation; it does not require a new route,
controller, policy branch, or page.

## Public Issuer

In the supported Compose deployment, `OAUTH_ISSUER` is the authoritative
connector issuer and must equal `PUBLIC_DASHBOARD_URL`. The Dashboard exposes
the discovery, OAuth, health, and memory paths on that origin and proxies them
server-side to the API through `API_INTERNAL_URL`. Ordinary Dashboard
API traffic can continue to use `PUBLIC_API_URL`.

Production deployments must set an explicit HTTPS `OAUTH_ISSUER` and matching
`PUBLIC_DASHBOARD_URL`, with no credentials, path, query, or fragment. An
absent issuer is accepted only when the API's server socket is verified as
loopback development. Reverse proxies must preserve the public scheme and
host, reject unexpected redirects, and route every connector path below to the
same Dashboard deployment. Do not expose the internal API origin as a second
issuer.

The initialization scripts independently generate
`OAUTH_USER_CODE_HMAC_SECRET` and `OAUTH_AUDIT_HMAC_SECRET`. The former protects
low-entropy user-code lookups; the latter hashes IP, user-agent, and rate-limit
context for shared audit enforcement. They are required secrets, must differ
from `JWT_SECRET`, and must be restored with the same application database.

Clients bootstrap only from a trusted, compiled issuer and fetch:

- `GET /.well-known/oauth-authorization-server`
- `GET /.well-known/service-capabilities`

The documents advertise these canonical paths on the issuer origin:

| Purpose | Path |
| --- | --- |
| Device authorization | `/oauth/device_authorization` |
| Token exchange and refresh | `/oauth/token` |
| Token revocation | `/oauth/revoke` |
| Connector health | `/oauth/health` |
| User verification | `/dashboard/connected-apps` |
| Memory search | `/search` |
| Memory write | `/memories` |
| Authenticated ping | `/v1/ping/` |

The capability contract is fixed to service ID `yiqiao`, audience
`yiqiao:memory-api`, scopes `memory:read` and `memory:write`, and protocol
version `1.0`. Device codes live for 600 seconds, access tokens for 900
seconds, and refresh tokens for 2,592,000 seconds. Clients must reject a
different issuer, origin, canonical path, or unsupported major protocol
version.

## Application Registration

Administrators manage data-only application records from the Connected Apps
Dashboard or the authenticated `/oauth/applications` API. A public application
has a stable `client_id`, display name, allowed audience and scopes, status,
and optional non-executable operator metadata. Public clients have no client
secret.

Grant the smallest scope set required by the client. Revoking an application
prevents new authorizations and invalidates its active grants. Management
responses expose display-safe identifiers, prefixes, status, scope, project,
and timestamps; they never expose token hashes, code hashes, plaintext
credentials, or internal rate-limit keys.

## Device Authorization

The client sends form-encoded `client_id`, `scope`, `audience`,
`code_challenge`, and `code_challenge_method=S256` to the discovered device
endpoint. The response contains a single-use device code, a human-entered user
code, the verification URI, expiry, and polling interval.

An authenticated user opens the verification page, reviews the application,
audience, requested scopes, and expiry, then selects an accessible project and
approves or rejects the request. Approval can reduce scopes but cannot add a
scope that was not requested and registered. The token endpoint returns
`authorization_pending`, `slow_down`, `access_denied`, or `expired_token`
until the request can be exchanged.

The issued Bearer token is bound to one user, application, audience, scope set,
and project. OAuth tokens are accepted only by exact `POST /memories`,
`POST /search`, and `GET /v1/ping/` requests. The resource server rejects
project identifiers supplied in a header, query, or body when they attempt to
override that binding. It also rejects expired or revoked credentials,
disabled applications, inaccessible projects, wrong audiences, and missing
scopes.

## Refresh and Revocation

Every successful refresh rotates both the access token and refresh token.
Reuse of a rotated refresh token revokes the entire token family. Clients must
serialize credential writes and coalesce concurrent refresh attempts.

RFC 7009 revocation accepts access or refresh tokens. Revoking a refresh token
revokes its family; an unknown token returns a successful empty response.
Users can also revoke one grant or every active grant for an application and
project from Connected Apps. Revocation is checked from shared PostgreSQL
state and takes effect across API processes.

## Operations

Alembic revision `018` creates the generic OAuth tables, removes active legacy
pairing state, and revokes only credentials tagged with the retired pairing
key type. Revision `017` remains byte-identical in migration history so
existing installations can upgrade safely. Back up the application PostgreSQL
database before upgrading; never stamp past revision `018` or run an older API
against the migrated database.

Expired device requests, retained refresh-token replay hashes, old grants, and
audit events are pruned in bounded batches. Schedule this command during
normal maintenance:

```text
cd server
make prune-oauth
```

`OAUTH_CLEANUP_BATCH_SIZE` defaults to `500`,
`OAUTH_AUDIT_RETENTION_DAYS` to `90`, and
`OAUTH_REFRESH_REPLAY_GRACE_SECONDS` to `86400`. Keep the replay grace long
enough for incident detection and keep audit records according to the
deployment's security and privacy policy. The cleanup command does not revoke
live grants.

## Security Controls

- Keep `AUTH_DISABLED=false`. Connector authorization is unavailable when the
  deployment is configured without normal user authentication.
- Store and back up PostgreSQL as sensitive credential state. Device, access,
  and refresh values are stored only as one-way hashes; the user-code lookup
  uses a keyed hash because user codes have low entropy.
- Terminate TLS at a trusted proxy, allow only the expected issuer host, and do
  not accept issuer or endpoint overrides from page content or imported data.
- Do not log authorization headers, form bodies, device codes, user codes, or
  tokens. OAuth and discovery responses use `Cache-Control: no-store`.
- Keep database-backed OAuth rate limits enabled on every replica. A local
  in-memory limiter is not a substitute for shared enforcement.
- Treat Connected Apps access as a privileged Dashboard operation. Review
  application registrations, active grants, denial/replay events, and cleanup
  retention regularly.

## Troubleshooting

First compare both discovery documents with the configured issuer. Every URL
must use the same origin and canonical path. A production startup error about
the issuer usually means `OAUTH_ISSUER` is missing, does not exactly match
`PUBLIC_DASHBOARD_URL`, is not HTTPS, or contains a path component that the
deployment cannot serve consistently.

For `invalid_client`, confirm the application is active and the `client_id`
matches exactly. For `invalid_scope` or `invalid_target`, compare the requested
scope and audience with the application record. For repeated `slow_down`, stop
extra pollers and obey the returned interval. For `access_denied` or
`project_scope_mismatch`, start a new device request and approve a project the
user can still access. A refresh-token replay requires a complete new device
authorization because the token family is revoked deliberately.

Use correlation identifiers and sanitized OAuth audit events for diagnosis.
Never paste credentials, raw request bodies, database rows, or proxy logs into
a public issue.
