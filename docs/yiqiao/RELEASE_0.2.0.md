> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

# YiQiao 0.2.0

[Simplified Chinese](RELEASE_0.2.0.zh-CN.md) | **English**

YiQiao 0.2.0 is a self-hosted release. It does not provide or depend on a
hosted YiQiao Dashboard service. Operators deploy the API, Dashboard,
Postgres, and Neo4j in their own environment.

## Included

- Public Service Connector 1.0 with OAuth Device Flow, PKCE, scoped grants,
  token rotation, revocation, and audit records.
- A refreshed responsive Dashboard for memories, entities, graph data,
  requests, webhooks, exports, settings, and connected applications.
- Same-origin Dashboard API transport so browser sessions no longer depend on
  a separately exposed API origin.
- Docker Compose deployment and upgrade documentation for local operators.

## Deployment

From a clean checkout, initialize secrets and start the self-hosted stack:

```bash
./scripts/init.sh
cd server
docker compose up -d
```

On Windows PowerShell:

```powershell
.\scripts\init.ps1
Set-Location server
docker compose up -d
```

The default local Dashboard is available at `http://localhost:3000`. See
[Operations](OPERATIONS.md) and [Troubleshooting](TROUBLESHOOTING.md) before
exposing any service publicly.

## Distribution Boundary

The public marketing website is maintained and deployed separately. Website
source, assets, and deployment configuration are intentionally excluded from
the YiQiao 0.2.0 open-source repository and release artifacts.
