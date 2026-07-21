# YiQiao
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

[简体中文](README.zh-CN.md) | **English**

YiQiao is a self-hosted memory service for AI assistants and agents. It combines
an authenticated REST API, an operations dashboard, semantic and graph-backed
memory, chat-history import, export, usage controls, and webhooks in one Docker
Compose deployment.

## Quick Start

Requirements: Git, Docker Desktop or Docker Engine with Docker Compose v2, and
outbound HTTPS access for pulling images and calling the model provider you
choose during setup. The Linux and macOS API examples require curl 7.76 or
newer.

Linux and macOS:

```bash
git clone https://github.com/daizhongming/YiQiao.git
cd YiQiao
./scripts/init.sh
cd server
docker compose up -d
```

Windows PowerShell:

```powershell
git clone https://github.com/daizhongming/YiQiao.git
Set-Location YiQiao
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
Set-Location server
docker compose up -d
```

Open <http://localhost:3000>. The first-run wizard creates the administrator,
configures the model and embedding providers, issues the first project API key,
and performs a memory write. The initialization script creates `server/.env`
and strong local secrets without overwriting an existing file.

| Service | Default address | Override |
| --- | --- | --- |
| Dashboard | <http://localhost:3000> | `DASHBOARD_PORT` |
| REST API | <http://localhost:8888> | `API_PORT` |
| OpenAPI | <http://localhost:8888/docs> | follows `API_PORT` |
| Health | <http://localhost:8888/api/health> | follows `API_PORT` |

Confirm that the stack is ready on Linux or macOS:

```bash
docker compose ps
curl --fail http://localhost:8888/api/health
```

On Windows PowerShell:

```powershell
docker compose ps
Invoke-RestMethod -Uri "http://localhost:8888/api/health"
```

The default Compose file pulls release images from GitHub Container Registry.
To build the API and dashboard from the checked-out source instead, run from
the repository root on Linux or macOS:

```bash
cd server
docker compose -f docker-compose.yaml -f docker-compose.build.yaml up -d --build
```

On Windows PowerShell:

```powershell
Set-Location server
docker compose -f docker-compose.yaml -f docker-compose.build.yaml up -d --build
```

<a id="verify-memory-add-and-search"></a>

## Verify Memory Add and Search

Complete the browser setup first and retain the API key shown once by the
wizard. The default project identifier is `default-project`.

Linux and macOS:

```bash
export YIQIAO_API_URL=http://localhost:8888
export YIQIAO_API_KEY='<your-api-key>'
export YIQIAO_PROJECT_ID=default-project

curl --fail-with-body -X POST "$YIQIAO_API_URL/memories" \
  -H "X-API-Key: $YIQIAO_API_KEY" \
  -H "X-Project-ID: $YIQIAO_PROJECT_ID" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"I prefer concise answers."}],"user_id":"quickstart-user"}'

curl --fail-with-body -X POST "$YIQIAO_API_URL/search" \
  -H "X-API-Key: $YIQIAO_API_KEY" \
  -H "X-Project-ID: $YIQIAO_PROJECT_ID" \
  -H "Content-Type: application/json" \
  -d '{"query":"How should answers be written?","filters":{"user_id":"quickstart-user"}}'
```

Windows PowerShell:

```powershell
$apiUrl = "http://localhost:8888"
$headers = @{
  "X-API-Key" = "<your-api-key>"
  "X-Project-ID" = "default-project"
}
$addBody = @{
  messages = @(@{ role = "user"; content = "I prefer concise answers." })
  user_id = "quickstart-user"
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri "$apiUrl/memories" -Headers $headers -ContentType "application/json" -Body $addBody

$searchBody = @{
  query = "How should answers be written?"
  filters = @{ user_id = "quickstart-user" }
} | ConvertTo-Json -Depth 4
Invoke-RestMethod -Method Post -Uri "$apiUrl/search" -Headers $headers -ContentType "application/json" -Body $searchBody
```

## Python Entry Point

Install the Python package from the checked-out source:

```bash
python -m pip install .
```

YiQiao provides synchronous and asynchronous Python entry points:

```python
from yiqiao import Memory, AsyncMemory
```

Local state is stored in `~/.yiqiao` by default and can be moved with
`YIQIAO_DIR`.

For a standalone service integration, use the REST API and project API keys
shown above.

## What YiQiao Provides

- Project-scoped memory add, search, update, delete, history, and feedback APIs.
- A dashboard for memories, entities, graph exploration, requests, API keys,
  configuration, usage limits, exports, imports, and webhooks.
- PostgreSQL with pgvector for application and vector data, plus optional Neo4j
  graph relationships.
- Browser-based provider configuration for bundled LLM and embedding adapters,
  including custom OpenAI-compatible base URLs.
- Authentication enabled by default, administrator onboarding, project API keys,
  role-aware workspace access, and request logging.
- Chat-history import with progress, retry, cancellation, and storage quotas.

Typical uses include persistent assistant preferences, support context, research
memory, coding-agent context, and private knowledge workflows where the operator
needs to own the storage and model-provider relationship.

## Architecture

```text
Browser / API clients
        |
        +--> Dashboard :3000
        |         |
        +---------+--> YiQiao API :8888 --> selected model providers
                             |  \
                             |   +--> Neo4j Community (graph)
                             +------> PostgreSQL + pgvector (auth, settings,
                                      requests, vectors, memory metadata)
```

Dashboard browser requests reach the API through `NEXT_PUBLIC_API_URL`; its
server-side requests use `API_INTERNAL_URL` over the internal Compose network.
PostgreSQL and Neo4j are internal-only by default. Database and graph state is
stored in named Docker volumes, memory-history SQLite and import workspace state
under `server/history/`, and deployment configuration and generated secrets in
`server/.env`. Export job records and results are stored in PostgreSQL; files
downloaded from the dashboard are retained by the client. Provider calls leave
the deployment over HTTPS when a remote provider is configured.

## Configuration

The recommended configuration path is the first-run wizard at
<http://localhost:3000/setup>. It discovers the providers bundled into the API
image and lets an administrator set provider, model, base URL, and API key.
Provider credentials are not required for containers to start, but memory
extraction and semantic search require a working LLM and embedder configuration.

Deployment settings live in `server/.env`. The generated defaults keep
authentication enabled, telemetry disabled, database services off host ports,
and secrets out of version control. Do not expose the API or dashboard directly
to the internet; terminate TLS at a trusted reverse proxy and restrict access to
the intended network.

See [Operations](docs/yiqiao/OPERATIONS.md) for ports, persistence, backup,
upgrade, source builds, and removal.

## Documentation

- [Operations](docs/yiqiao/OPERATIONS.md)
- [Migration](docs/yiqiao/MIGRATION.md)
- [Troubleshooting](docs/yiqiao/TROUBLESHOOTING.md)
- [Licensing and provenance](docs/yiqiao/LEGAL.md)
- [Security policy](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)
- [Modification notices](MODIFICATIONS.md)

## Known Limits

- The default deployment is a single-host Compose stack, not a high-availability
  cluster.
- Neo4j Community does not provide the enterprise online-backup and clustering
  features. Plan a maintenance window for graph-volume snapshots.
- Provider behavior, privacy, rate limits, and data residency remain the
  operator's responsibility.
- Version `0.1.x` is an initial open-source product line; deployment and API
  compatibility should be reviewed before each upgrade.

Current roadmap priorities are reproducible image provenance and SBOMs,
documented external database deployments, stronger backup automation, and a
versioned compatibility policy. Roadmap items are directional and have no
committed delivery date.

## License and Third-Party Notices

YiQiao is an independently maintained and released open-source product under
the Apache License 2.0. See [LICENSE](LICENSE), [NOTICE](NOTICE),
[Third-Party Notices](THIRD_PARTY_NOTICES.md), and
[Modification Notices](MODIFICATIONS.md) for licensing, attribution, and the
record of changes.
