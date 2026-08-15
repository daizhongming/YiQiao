> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

# YiQiao 1.0.0

[Simplified Chinese](RELEASE_1.0.0.zh-CN.md) | **English**

YiQiao 1.0.0 is the first stable release of the self-hosted YiQiao memory
service. It combines the REST API, operations dashboard, MCP companion, graph
memory, chat-history import, exports, usage controls, and webhooks in one
versioned release line.

## Highlights

- Stable `1.0.x` compatibility line with synchronized versions for the Python
  package, REST API, dashboard, and MCP companion.
- Project-scoped API-key authentication, administrator onboarding, role-aware
  workspaces, request logging, quotas, and webhook delivery.
- Semantic and graph-backed memory workflows with add, search, update, delete,
  history, feedback, and exact-duplicate cleanup.
- Chat-history import with progress reporting, retries, cancellation, storage
  quotas, deterministic resume keys, and isolated workspace state.
- Dashboard and MCP contract coverage for the supported browser and agent
  workflows, including safe untrusted-recall markers.
- Reproducible Compose overlays, production hardening, legal payload checks,
  dependency checks, and multi-language documentation validation in CI.

## Compatibility and upgrade

YiQiao 1.0.0 keeps the existing REST resource model and database migration
history from the 0.2.x line. Before upgrading, make a complete backup of the
PostgreSQL databases, Neo4j volume, `server/history/`, and `server/.env` as
described in [Operations](OPERATIONS.md). Keep the current embedding model and
vector dimensions for the first start.

Pull the versioned images or check out the release tag, then start the stack
with the production overlay:

```bash
git checkout v1.0.0
cd server
docker compose -f docker-compose.yaml -f docker-compose.production.yaml up -d
docker compose ps
curl --fail http://localhost:8888/api/health
```

After the services are healthy, sign in at `http://localhost:3000`, verify the
provider configuration, and run the memory add/search example in the root
README. Do not expose the API, dashboard, PostgreSQL, or Neo4j directly to the
public internet; use a TLS reverse proxy or private network.

## Validation

The release candidate was checked with the Python core/server suites, MCP
companion and contract suites, dashboard lint/typecheck/unit tests, dashboard
production build, documentation localization, source compilation, and package
metadata validation. Optional provider adapters still require their own
credentials and external services.

See [Migration](MIGRATION.md), [Troubleshooting](TROUBLESHOOTING.md), and
[Operations](OPERATIONS.md) for rollback and recovery procedures.
