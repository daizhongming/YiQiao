# Security Policy
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

[简体中文](SECURITY.zh-CN.md) | **English**

## Report a Vulnerability

Do not disclose suspected vulnerabilities in a public issue, pull request,
discussion, chat transcript, or log attachment.

Use GitHub Private Vulnerability Reporting as the private channel:

<https://github.com/daizhongming/YiQiao/security/advisories/new>

Do not send vulnerability details, proof of concepts, secrets, tokens, personal
data, or sensitive logs through any public repository channel. If the private
form is temporarily unavailable, open a regular issue containing only a
non-sensitive request for a maintainer to restore the private channel, then wait
for a private channel before sharing sensitive details.

Include, when possible:

- The affected YiQiao version, commit, and container image digest.
- The affected component and deployment topology.
- Reproduction steps or a minimal proof of concept.
- Expected and observed behavior, impact, and required attacker access.
- Relevant logs with tokens, credentials, personal data, and hostnames redacted.
- A proposed mitigation or patch, if available.

## Response and Disclosure

Maintainers aim to acknowledge a report within five business days, confirm its
scope, and coordinate remediation and disclosure with the reporter. Complex
issues may require more time. Credit will be offered unless the reporter asks to
remain anonymous.

Please allow time for a fix and an operator advisory before public disclosure.
For an actively exploited issue or imminent risk, state that clearly in the
private report.

## Supported Versions

Security fixes target the latest YiQiao release line. Older versions and local
development snapshots may receive guidance but are not guaranteed patches.
Before the first tagged release, reports should be reproduced against the head
of `main` when practical.

## Operator Responsibilities

YiQiao is self-hosted. Operators are responsible for:

- Keeping authentication enabled and rotating generated secrets after exposure.
- Restricting the dashboard and API to trusted networks or a TLS reverse proxy.
- Protecting `server/.env`, database volumes, backups, provider credentials, and
  API keys.
- Reviewing provider data handling, retention, residency, and model safety.
- Applying YiQiao, base-image, PostgreSQL, Neo4j, and dependency updates.
- Testing backup restoration and monitoring authentication and request logs.

The default Compose deployment does not make PostgreSQL or Neo4j host-accessible.
Do not publish those ports without an explicit network and authentication review.

## Public Connector Security Boundary

Public Service Connector Protocol `1.0` supports registered public clients
through user-mediated OAuth Device Authorization with PKCE S256 and rotating
refresh tokens. Tokens are bound to a user, application, audience, scope set,
and project; operators must preserve those checks at the reverse proxy and API
and must not expose a broader route as a connector resource.

Protect `OAUTH_DEVICE_CODE_SECRET`, `OAUTH_AUDIT_HMAC_SECRET`, and
`OAUTH_PROXY_HMAC_SECRET` independently from each other, `JWT_SECRET`, and
`ADMIN_API_KEY`. Treat the application
database and its backups as credential state, retain replay evidence for the
configured grace period, keep shared rate limits enabled on every replica, and
never place tokens, device/user codes, PKCE material, or authorization headers
in logs or support bundles.

The Dashboard overwrites caller-provided transport identity and signs the peer
used by API rate limiting. Enable `OAUTH_GATEWAY_RATE_LIMIT_CONFIRMED` only when
the sole ingress gateway sanitizes `X-Forwarded-For` to one client IP and
enforces equivalent per-IP limits; a spoofable forwarding header is not a trust
boundary.

Client Credentials and RFC 8693 Token Exchange are not implemented. MCP
Streamable HTTP is an ADR-only evaluation, not an available transport or
endpoint. Neither service-to-service work nor a future MCP transport may bypass
OAuth or project isolation. The complete review contract is in
[Secret Scanning and Connector Security Review](docs/yiqiao/SECURITY_AUDIT.md).
