# Secret Scanning

**English** | [简体中文](SECURITY_AUDIT.zh-CN.md)

YiQiao scans both the current source tree and the complete public Git history
with Gitleaks 8.28.0. The release history starts with the target repository's
placeholder commit and adds the reviewed YiQiao source as a single snapshot; it
does not merge or retain the upstream repository's commit graph.

The public release contains no `.gitleaksignore`. Every YiQiao commit must pass
without commit, path, rule, or line fingerprint exceptions. The narrow rule in
`.gitleaks.toml` permits only adjacent empty provider-key entries in the checked
in environment template. Supplying either value stops the rule from matching.

Run the release checks with:

```bash
gitleaks dir --redact=100 --no-banner .
gitleaks git --redact=100 --no-banner .
```

Generate a fully redacted report outside the repository when investigating a
failure:

```bash
gitleaks git --redact=100 --no-banner --report-format json \
  --report-path /tmp/yiqiao-gitleaks-review.json .
```

Gitleaks exits with status 1 when it writes a report containing findings. Fix
or remove the detected material; do not add fingerprint exceptions merely to
make CI pass. Runtime state such as `.env`, `server/history`, logs, browser
artifacts, database dumps, and backups is absent from a clean checkout and must
never be committed or allowlisted.

## Public Connector Security Review

Secret scanning does not prove that the OAuth boundary is correct. A connector
release or deployment is **NO-GO** unless review evidence covers all of these
controls against the real API and shared PostgreSQL state:

- A production `OAUTH_ISSUER` is explicit HTTPS, exactly matches the external
  Dashboard origin, and is not derived from untrusted `Host` or forwarded
  headers. Discovery and capability URLs remain on that one trusted origin.
- `/api/health` is the unauthenticated process-health endpoint.
  `GET /v1/ping/` is a protected resource and requires a valid project-bound
  OAuth token with memory scope. Only exact `POST /memories`, `POST /search`,
  and `GET /v1/ping/` resource routes accept connector tokens.
- The Device Authorization Grant requires a pre-registered active public
  client, form encoding, a registered audience and scopes, and PKCE S256.
  Approval may reduce scopes but cannot add scopes.
- Plaintext device codes, access tokens, and refresh tokens appear only in the
  response that creates them. PostgreSQL stores one-way hashes. Low-entropy
  user-code lookup uses `OAUTH_DEVICE_CODE_SECRET`, which must be independent
  from `JWT_SECRET`, `OAUTH_AUDIT_HMAC_SECRET`, `OAUTH_PROXY_HMAC_SECRET`, and
  `ADMIN_API_KEY`; all three OAuth HMAC secrets are independent, and production
  must not fall back to another secret.
- Every protected request rechecks application and grant status, expiry,
  audience, scope, bound project, and the user's current project role. Header,
  query, body, metadata, or search-filter project overrides fail closed.
- Refresh rotation is atomic. Reuse of a rotated refresh token revokes the
  entire family, and revocation takes effect from shared state on every API
  process. Failed requests do not update successful-use timestamps.
- Device authorization, token, revocation, user-code lookup and approval, and
  application registration have shared, transactional limits by relevant IP,
  client, and pending-authorization count. A `429` includes `Retry-After`.
  Per-process memory limits are not evidence of multi-replica enforcement.
- The Dashboard removes caller-supplied forwarding and internal context headers,
  signs its normalized transport peer, method, path/query, and timestamp with
  `OAUTH_PROXY_HMAC_SECRET`, and the API rejects partial, stale, or tampered
  contexts. `OAUTH_GATEWAY_RATE_LIMIT_CONFIRMED=true` is valid only when the
  sole ingress gateway replaces `X-Forwarded-For` with exactly one validated
  client IP and enforces equivalent per-IP limits; pass-through and append-only
  proxies must leave it false.
- Audit events and application-management responses omit credentials, token or
  code hashes, PKCE verifiers, authorization headers, form bodies, raw IPs, and
  other secrets. Retention and bounded cleanup preserve refresh-replay evidence
  for the configured grace period.

Use two unrelated registered public clients when validating isolation. Include
negative evidence for wrong PKCE, scope, audience, cross-client and
cross-project use, project overrides, changed roles, revocation, replay,
rate limiting, cleanup, and concurrent exchange/refresh/revoke. SQLite is only
a fast local check; concurrency and shared enforcement require a dedicated,
isolated PostgreSQL test database.

## Grant and Transport Boundaries

The implemented user flow is OAuth Device Authorization with refresh-token
rotation. Client Credentials (`grant_type=client_credentials`) and RFC 8693
Token Exchange are service-to-service designs and are not implemented. The
presence of `/oauth/token` does not imply support for either flow.

MCP Streamable HTTP is ADR-only evaluation material. There is no MCP transport
or endpoint in this release. Any future MCP design remains subject to the same
OAuth checks and project isolation; an MCP path must never bypass them. See
[Public Connector](PUBLIC_CONNECTOR.md) for the protocol contract and
[Troubleshooting](TROUBLESHOOTING.md) for safe diagnosis.
