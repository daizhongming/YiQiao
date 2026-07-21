# YiQiao Server
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

[简体中文](README.zh-CN.md) | **English**

This directory contains the YiQiao API, dashboard, database migrations, and
Docker Compose deployment. The canonical product Quick Start is in the
[repository README](../README.md).

## Quick Start

Run initialization from the repository root. The initializer copies
`server/.env.example`, generates independent `POSTGRES_PASSWORD`,
`NEO4J_PASSWORD`, and `JWT_SECRET` values, and preserves an existing `.env`.
Provider credentials are configured in the browser and do not block service
startup.

Linux and macOS:

```bash
./scripts/init.sh
cd server
docker compose up -d
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
Set-Location server
docker compose up -d
```

Open <http://localhost:3000> and complete the first-run setup. The default
service addresses are:

| Service | Address | Override |
| --- | --- | --- |
| Dashboard | <http://localhost:3000> | `DASHBOARD_PORT` |
| API | <http://localhost:8888> | `API_PORT` |
| OpenAPI | <http://localhost:8888/docs> | follows `API_PORT` |
| Health | <http://localhost:8888/api/health> | follows `API_PORT` |

The default deployment pulls the configured release images. To build the API
and dashboard from this checkout, run the following from the repository root in
Bash or PowerShell:

```text
cd server
docker compose -f docker-compose.yaml -f docker-compose.build.yaml up -d --build
```

`make` targets remain optional maintenance shortcuts; they are not required
for installation.

## Security and Persistence

Authentication is enabled and telemetry is disabled by default. The API and
dashboard bind to loopback, while PostgreSQL and Neo4j remain on the internal
backend network without host port mappings. Do not expose the services
directly to the internet; use a trusted TLS reverse proxy or private network.

Persistent application, vector, and export-record data is stored in the
`postgres_db` volume, graph data in `neo4j_data`, and memory-history SQLite plus
import workspaces under `server/history/`. Dashboard downloads are client-side
files. Treat `server/.env`, database backups, graph snapshots, history files,
provider credentials, and request logs as sensitive.

Database migrations run automatically before the API starts. A migration
failure is a hard stop; do not stamp past it or attach an older API to the only
copy of migrated data.

## Documentation

- [Operations, backup, upgrade, rollback, and removal](../docs/yiqiao/OPERATIONS.md)
- [Migration](../docs/yiqiao/MIGRATION.md)
- [Troubleshooting](../docs/yiqiao/TROUBLESHOOTING.md)
- [Security policy](../SECURITY.md)
- [API add/search examples](../README.md#verify-memory-add-and-search)
