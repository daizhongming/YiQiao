# YiQiao Troubleshooting

Run diagnostics from `server/`. Keep logs and configuration private because they
can contain prompts, identifiers, credentials, and provider details.

## Diagnostic Order

```bash
docker compose config --quiet
docker compose ps
docker compose logs --tail=200
curl --fail-with-body http://localhost:8888/api/health
```

Inspect the first unhealthy dependency rather than repeatedly restarting the
whole stack. PostgreSQL and Neo4j must be healthy before the API, and the API
must be healthy before the dashboard.

## Initialization Reports Missing Secrets

Run the platform initializer from the repository root:

```bash
./scripts/init.sh
```

PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
```

It fills only missing required secrets. If `server/.env` exists with malformed
or intentionally invalid values, back it up securely, compare it with
`.env.example`, and correct the values rather than deleting unknown data.

## Images Cannot Be Pulled

Check registry reachability and the requested image tag:

```bash
docker compose pull
```

Public images do not require registry credentials. Check package visibility and
the exact tag in GHCR, or test anonymous access from a disposable environment
that has no registry credentials. Do not run `docker logout` in a shared user
profile because it also removes credentials for unrelated private packages. For
an unpublished or private tag, use an authorized published tag or build the
checkout with:

```bash
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.build.yaml \
  up -d --build
```

## Port Already in Use

Find the listener with the host's network tools, or change the loopback ports in
`server/.env`:

```dotenv
API_PORT=8889
DASHBOARD_PORT=3001
```

When using a reverse proxy, update `PUBLIC_API_URL` and
`PUBLIC_DASHBOARD_URL` as well. Recreate the containers:

```bash
docker compose up -d --force-recreate
```

## PostgreSQL Is Unhealthy

```bash
docker compose logs --tail=200 postgres
docker compose exec postgres pg_isready -U postgres -d postgres
```

Common causes are a password mismatch with an existing volume, a full disk,
incorrect file ownership after a manual volume copy, or an interrupted restore.
Changing `POSTGRES_PASSWORD` in `.env` does not change the password already
stored in an initialized database volume.

Do not delete the volume to clear the error unless the data is disposable or a
restore has been verified.

## Neo4j Is Unhealthy

```bash
docker compose logs --tail=200 neo4j
docker compose exec neo4j cypher-shell -u neo4j -p '<password>' 'RETURN 1'
```

Use the value from `server/.env` without posting it to a shell history on a
shared system. A password mismatch with an existing volume, insufficient disk,
or an unclean copied volume are common causes. Neo4j Community can take longer
to become healthy after recovery; inspect logs before extending timeouts.

## API Is Unhealthy or Migrations Fail

```bash
docker compose logs --tail=300 yiqiao
docker compose exec yiqiao alembic current
docker compose exec yiqiao alembic heads
```

The current revision should reach the single repository head. Do not stamp or
skip a failed revision merely to make the container start. Restore a test copy of
the database and reproduce the migration there.

## Dashboard Loads but API Calls Fail

Confirm both browser-visible and internal URLs:

```bash
docker compose exec yiqiao-dashboard \
  wget -qO- http://yiqiao:8000/api/health
curl --fail http://localhost:8888/api/health
```

Behind a proxy, `PUBLIC_API_URL` must be reachable by the browser and
`PUBLIC_DASHBOARD_URL` must match the external dashboard origin. Recreate the
dashboard after changing public URLs.

## Setup or Login Fails

Only the first account can use the registration endpoint. If an administrator
already exists, sign in rather than rerunning registration. Check API auth logs
for status `401`, `403`, or rate limiting without exposing request bodies.

Reset a known administrator password from the API container:

```bash
docker compose exec \
  -e EMAIL='admin@example.com' \
  -e PASSWORD='<new-strong-password>' \
  yiqiao python scripts/reset_admin_password.py
```

Run this only from a trusted terminal. The password can be captured in shell or
process history; rotate it again through the dashboard afterward when practical.

## API Key Returns 401 or 403

- Copy the complete key when it is first created; only its prefix is displayed
  later.
- Send it as `X-API-Key`.
- Send the intended project as `X-Project-ID`; the default is
  `default-project`.
- Confirm the key has not been revoked and belongs to that project.
- Use an administrator session, not a project key, for control-plane settings.

## Provider Connection Fails

Use the Configuration page connection tests for the LLM and embedder separately.
Check provider name, model, base URL, API-key scope, outbound DNS/TLS, rate limits,
and request timeout. OpenAI-compatible endpoints may require a base URL ending in
`/v1`; follow that provider's contract.

Provider keys saved through the dashboard are persisted in PostgreSQL. A
container restart does not clear them. Replace or delete a stale credential
through an authenticated configuration flow rather than exposing the database
row.

## Embedding Dimension Errors

The configured dimension must match both the provider response and the existing
vector collection. Revert to the original model and dimension to restore access.
Changing dimensions for existing data requires a new collection and an explicit
re-embedding migration; it cannot be repaired by restarting containers.

## Search Does Not Return a New Memory

1. Confirm the add response succeeded rather than returning a provider error.
2. Use the same project and entity filters for add and search.
3. Check whether extraction is enabled and whether the input produced a fact.
4. Inspect API request logs and provider tests.
5. Search without a threshold or reranker to isolate retrieval from ranking.

## Graph Is Empty or Unavailable

Check Neo4j health, graph status in the dashboard, and whether graph memory is
enabled. Vector memory can exist even when graph synchronization failed. Use the
graph retry or sync control only after correcting connectivity or extraction
errors; repeated retries can consume provider quota.

## Chat Import Stalls or Fails

Check the import job phase, errors, lease state, configured provider route,
available history storage, active-job quota, and API logs. Do not edit files in
an active job workspace. Retry failed chunks only after fixing the cause; discard
retained source files after validation when they are no longer needed.

## Disk Usage Grows

Inspect Docker and history usage:

```bash
docker system df
docker volume ls --filter label=com.docker.compose.project=yiqiao-v3
du -sh history 2>/dev/null || true
```

Use dashboard retention and import-discard controls before manual pruning. Never
run broad Docker prune commands on a host with unrelated workloads. Back up and
identify an absolute path or volume before deletion.

## Collecting a Support Bundle

There is no automatic safe support bundle. Record the commit, image digests,
`docker compose ps`, failing command, status code, and a minimal redacted log
excerpt. Do not include `.env`, database dumps, provider configuration, API keys,
JWTs, cookies, prompts, import files, or personal data in a public issue.
