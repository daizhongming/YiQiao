# YiQiao Operations

[简体中文](OPERATIONS.zh-CN.md) | **English**

This runbook covers the supported single-host Docker Compose deployment. Run
commands from the repository root unless a section says otherwise.

## Requirements

- A 64-bit Linux, macOS, or Windows host supported by Docker.
- Docker Desktop, or Docker Engine with the Docker Compose v2 plugin.
- Git for source-based installation and upgrades.
- Bash for the Backup and Restore procedures. On Windows, run those
  maintenance sections from WSL 2 with container-engine integration enabled.
- Outbound HTTPS for the container registry and any remote model provider.
- Available loopback ports `3000` and `8888`, or alternate values in
  `server/.env`.
- Storage sized for PostgreSQL vectors, Neo4j graph data, request logs, exports,
  and retained import files.

Confirm the tooling before initialization:

```bash
docker version
docker compose version
```

## Initialize

Linux and macOS:

```bash
./scripts/init.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1
```

The script copies `server/.env.example` to `server/.env` when needed, generates
`POSTGRES_PASSWORD`, `NEO4J_PASSWORD`, `JWT_SECRET`,
`OAUTH_USER_CODE_HMAC_SECRET`, and `OAUTH_AUDIT_HMAC_SECRET`, creates
`server/history`, and validates Compose. It preserves non-empty secrets and
does not replace an existing environment file.

Treat `server/.env` as a secret. Do not commit it, attach it to an issue, or keep
an unencrypted copy with database backups.

## Start and Stop

The default mode pulls published API and dashboard images:

```bash
cd server
docker compose up -d
docker compose ps
```

Build the application images from the current checkout:

```bash
cd server
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.build.yaml \
  up -d --build
```

Apply the production hardening overlay to force authentication, drop application
container capabilities, and bound Docker log rotation:

```bash
cd server
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.production.yaml \
  up -d
```

PowerShell accepts the same `docker compose` arguments on one line.

Stop containers while retaining data:

```bash
cd server
docker compose stop
```

Remove containers and networks while retaining data:

```bash
cd server
docker compose down
```

## Endpoints and Network Exposure

| Setting                  | Default                       | Purpose                                |
| ------------------------ | ----------------------------- | -------------------------------------- |
| `API_BIND_ADDRESS`       | `127.0.0.1`                   | Host interface for the API             |
| `API_PORT`               | `8888`                        | Host API port                          |
| `DASHBOARD_BIND_ADDRESS` | `127.0.0.1`                   | Host interface for the dashboard       |
| `DASHBOARD_PORT`         | `3000`                        | Host dashboard port                    |
| `PUBLIC_API_URL`         | derived from `API_PORT`       | Browser-visible API URL behind a proxy |
| `PUBLIC_DASHBOARD_URL`   | derived from `DASHBOARD_PORT` | Public dashboard origin and auth URL   |
| `OAUTH_ISSUER`           | derived from the dashboard URL | Public connector issuer origin         |

PostgreSQL and Neo4j have no host port mapping. They are attached only to the
internal backend network. The dashboard and API listen on loopback by default.

For remote access, prefer a TLS reverse proxy or private network. Set the public
URLs to their externally visible HTTPS values, keep the bind addresses as narrow
as the proxy topology permits, and restart the application containers. Do not
expose database services directly.

Public connector deployments must set `OAUTH_ISSUER` and
`PUBLIC_DASHBOARD_URL` to the same external HTTPS origin. Route discovery,
OAuth, connector health, and the advertised memory paths through that origin;
keep the API's internal origin private. See [Public Connector](PUBLIC_CONNECTOR.md)
for the complete trust, token, cleanup, and audit contract.

## Application Images

| Setting                  | Default                                        |
| ------------------------ | ---------------------------------------------- |
| `YIQIAO_API_IMAGE`       | `ghcr.io/daizhongming/yiqiao-api:latest`       |
| `YIQIAO_DASHBOARD_IMAGE` | `ghcr.io/daizhongming/yiqiao-dashboard:latest` |
| `YIQIAO_PULL_POLICY`     | `always`                                       |

For reproducible production deployments, replace `latest` with a reviewed tag or
immutable digest. Resolve the configured application image references and record
both registry digests before an upgrade:

```bash
cd server
set -- $(docker compose config --images yiqiao yiqiao-dashboard)
if [ "$#" -ne 2 ]; then
  echo "Expected two resolved application images" >&2
  exit 1
fi
printf 'Resolved image: %s\n' "$@"
docker image inspect --format '{{index .RepoDigests 0}}' "$@"
```

## First-Run Setup and Providers

Open <http://localhost:3000/setup> after all services are healthy. The wizard:

1. Creates the first administrator.
2. Selects and configures the bundled LLM and embedding providers.
3. Creates the first project API key.
4. Records the intended use case.
5. Performs a memory write.

The provider list comes from the running API image. The configuration page can
also set custom OpenAI-compatible base URLs and model identifiers. Use its
connection tests before processing production data.

Provider API keys saved through the dashboard are stored in the application
database as runtime configuration. They are redacted from configuration API
responses, but they are not a substitute for an external secret manager. Protect
the PostgreSQL volume, logical dumps, administrator sessions, and dashboard
access accordingly. For stricter deployments, inject provider credentials
through the environment and restrict configuration permissions.

Provider credentials are not required for container startup. Memory extraction,
embedding, reranking, and import operations fail until their configured routes
are usable.

## Persistence

| Location                     | Contents                                                                                                   | Lifecycle                                               |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| Compose volume `postgres_db` | Users, API keys, OAuth applications/grants/audit, settings, requests, exports, and vector data                 | Retained by `docker compose down`; deleted by `down -v` |
| Compose volume `neo4j_data`  | Graph entities and relationships                                                                           | Retained by `docker compose down`; deleted by `down -v` |
| `server/history/`            | Memory-history SQLite, import workspaces, retained source files, and local runtime artifacts               | Host directory; back up and prune separately            |
| `server/.env`                | Deployment settings and secrets                                                                            | Host file; preserve securely and never commit           |

Export job records and results are stored in the application PostgreSQL
database. Files downloaded from the dashboard exist only where the browser or
API client saves them and require a separate client-side retention policy.

Set `PROJECT` to the exact Compose project used by the active deployment. The
repository default is `yiqiao-v3`; an explicit `-p` value overrides it. Keep the
same value on every backup command, and discover the actual volume names from
their project labels:

```bash
cd server
PROJECT=yiqiao-v3
docker compose -p "$PROJECT" config --volumes
docker volume ls --filter "label=com.docker.compose.project=$PROJECT"
```

## Backup

Back up before every upgrade and test restoration on a separate host. A complete
backup includes both PostgreSQL databases, the Neo4j volume, `server/history`,
and an encrypted copy of the environment settings. The application database
dump includes server-side export job records and results; separately retain any
files that users downloaded to client devices.

Use a maintenance window so PostgreSQL, Neo4j, and history represent the same
quiescent application state. Replace database and user names if the environment
overrides their defaults. A fresh Compose volume creates only `yiqiao_app`; an
advanced `APP_DB_NAME` override must already exist on the target server.

```bash
set -euo pipefail
mkdir -p backups
cd server
PROJECT=yiqiao-v3
docker compose -p "$PROJECT" stop yiqiao-dashboard yiqiao neo4j
docker compose -p "$PROJECT" exec -T postgres \
  pg_dump -U postgres -d postgres --format=custom \
  --file=/tmp/yiqiao-memory.dump
docker compose -p "$PROJECT" exec -T postgres \
  pg_dump -U postgres -d yiqiao_app --format=custom \
  --file=/tmp/yiqiao-application.dump
docker compose -p "$PROJECT" cp postgres:/tmp/yiqiao-memory.dump ../backups/yiqiao-memory.dump
docker compose -p "$PROJECT" cp postgres:/tmp/yiqiao-application.dump ../backups/yiqiao-application.dump
docker compose -p "$PROJECT" exec -T postgres \
  rm -f /tmp/yiqiao-memory.dump /tmp/yiqiao-application.dump
docker volume ls \
  --filter "label=com.docker.compose.project=$PROJECT" \
  --filter label=com.docker.compose.volume=neo4j_data
```

Neo4j Community does not provide the Enterprise online-backup workflow. With the
application and Neo4j still stopped, take a snapshot of the listed
`neo4j_data` volume using the host or storage-provider snapshot facility. Then
archive history while imports and all other application writes remain stopped:

```bash
cd server
tar --create --gzip --file ../backups/yiqiao-history.tar.gz history
```

Store `server/.env` separately in an encrypted secret backup. Never publish the
environment, database dumps, graph snapshot, history archive, or downloaded
exports. Restart only after every artifact has been verified as present and
non-empty:

```bash
cd server
PROJECT=yiqiao-v3
docker compose -p "$PROJECT" up -d
```

## Restore

Perform restoration into an isolated replacement deployment with new, empty
volumes. Keep the original deployment and volumes unchanged until validation
succeeds. Initialize `server/.env`, but do not start the API, dashboard, or
Neo4j before the data is restored. Use a physically separate checkout so its
`server/history` bind mount and `.env` cannot overlap the active deployment.
Run the commands below in Bash; Windows operators must use the WSL 2 environment
described in Requirements.

Start only the replacement PostgreSQL service. Its first-run initializer creates
empty default databases without running YiQiao migrations. Copy and restore both
custom-format dumps before any API process can connect. Set `ACTIVE_PROJECT` to
the exact live project used for the backup, and replace the dated
`RESTORE_PROJECT` example with a unique lowercase name.

Before running the block, selectively merge the encrypted secret backup into the
replacement `.env`. Keep the newly initialized `POSTGRES_PASSWORD`, because the
replacement PostgreSQL cluster owns that credential. Restore the source
`NEO4J_USERNAME` and `NEO4J_PASSWORD` required by the Neo4j snapshot, plus the
source `JWT_SECRET`, `OAUTH_USER_CODE_HMAC_SECRET`,
`OAUTH_AUDIT_HMAC_SECRET`, and any required provider secrets. Do not copy the
old `.env` wholesale or replace the restore checkout's bind addresses and paths.
Complete this merge before `create neo4j` captures its environment. For a
side-by-side restore, set `ACTIVE_SERVER_DIR` to the live checkout. Set it to an
empty string only when restoring on a separate host where the active checkout
and volume cannot exist; all restore-target emptiness and identity checks still
apply:

Maintain `PROTECTED_SOURCE_VOLUMES` as a reviewed file containing one retained
source volume name per line. Original source volumes and bind-mounted database
directories are read-only evidence: do not start them, attach them to replacement
services, or include them in any deletion manifest. Record bind-mount source
paths separately and apply the same exclusion when reviewing rendered Compose
configuration.

```bash
set -euo pipefail
ACTIVE_SERVER_DIR=/srv/yiqiao-live/server
RESTORE_SERVER_DIR="$(cd /srv/yiqiao-restore/server && pwd -P)"
BACKUP_DIR="$(cd /secure/path/to/yiqiao-backups && pwd -P)"
PROTECTED_SOURCE_VOLUMES=/secure/path/to/protected-source-volumes.txt
test -f "$PROTECTED_SOURCE_VOLUMES"
if test -n "$ACTIVE_SERVER_DIR"; then
  ACTIVE_SERVER_DIR="$(cd "$ACTIVE_SERVER_DIR" && pwd -P)"
  test "$RESTORE_SERVER_DIR" != "$ACTIVE_SERVER_DIR"
fi
cd "$RESTORE_SERVER_DIR"
test "$(pwd -P)" = "$RESTORE_SERVER_DIR"
test -f .env
test -d history
test -z "$(find history -mindepth 1 -print -quit)"
test -s "$BACKUP_DIR/yiqiao-memory.dump"
test -s "$BACKUP_DIR/yiqiao-application.dump"
test -s "$BACKUP_DIR/yiqiao-history.tar.gz"
ACTIVE_PROJECT=yiqiao-v3
RESTORE_PROJECT=yiqiao-restore-20260719
test -n "$RESTORE_PROJECT"
test "$RESTORE_PROJECT" != "$ACTIVE_PROJECT"
test -z "$(docker compose -p "$RESTORE_PROJECT" ps -aq)"
test -z "$(docker network ls -q \
  --filter "label=com.docker.compose.project=$RESTORE_PROJECT")"
for VOLUME in postgres_db neo4j_data; do
  if docker volume inspect "${RESTORE_PROJECT}_${VOLUME}" >/dev/null 2>&1; then
    echo "Refusing to reuse existing volume ${RESTORE_PROJECT}_${VOLUME}" >&2
    exit 1
  fi
done

docker compose -p "$RESTORE_PROJECT" up -d postgres
RESTORE_VOLUME="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$RESTORE_PROJECT" \
  --filter label=com.docker.compose.volume=postgres_db)"
test "$RESTORE_VOLUME" = "${RESTORE_PROJECT}_postgres_db"
test "$(docker volume inspect --format \
  '{{index .Labels "com.docker.compose.project"}}' "$RESTORE_VOLUME")" = \
  "$RESTORE_PROJECT"
test "$(docker volume inspect --format \
  '{{index .Labels "com.docker.compose.volume"}}' "$RESTORE_VOLUME")" = \
  postgres_db
if grep -Fqx -- "$RESTORE_VOLUME" "$PROTECTED_SOURCE_VOLUMES"; then
  echo "Refusing to use protected source volume $RESTORE_VOLUME" >&2
  exit 1
fi

POSTGRES_READY=false
for _ in {1..60}; do
  if docker compose -p "$RESTORE_PROJECT" exec -T postgres \
    pg_isready -q -h 127.0.0.1 -d postgres -U postgres; then
    POSTGRES_READY=true
    break
  fi
  sleep 2
done
test "$POSTGRES_READY" = true
test "$(docker compose -p "$RESTORE_PROJECT" exec -T postgres \
  psql -U postgres -d postgres -Atqc \
  "SELECT count(*) FROM pg_database WHERE datname IN ('postgres', 'yiqiao_app')")" = 2
for DATABASE in postgres yiqiao_app; do
  test "$(docker compose -p "$RESTORE_PROJECT" exec -T postgres \
    psql -U postgres -d "$DATABASE" -Atqc \
    "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')")" = 0
done
```

**Destructive restore boundary:** the next commands use `pg_restore --clean`.
They are valid only in the unique replacement project after the database
existence, table-emptiness, project-label, and protected-source checks above have
all passed. They are forbidden for a non-empty target, an in-place upgrade, or a
selective legacy merge. Return to [Migration](MIGRATION.md) for those workflows.

Continue in the same shell:

```bash

docker compose -p "$RESTORE_PROJECT" cp "$BACKUP_DIR/yiqiao-memory.dump" postgres:/tmp/yiqiao-memory.dump
docker compose -p "$RESTORE_PROJECT" cp "$BACKUP_DIR/yiqiao-application.dump" postgres:/tmp/yiqiao-application.dump
# Destructive replacement restore. Never use this block as a merge.
docker compose -p "$RESTORE_PROJECT" exec -T postgres pg_restore \
  --exit-on-error --clean --if-exists --no-owner --no-privileges \
  --username postgres --dbname postgres /tmp/yiqiao-memory.dump
docker compose -p "$RESTORE_PROJECT" exec -T postgres pg_restore \
  --exit-on-error --clean --if-exists --no-owner --no-privileges \
  --username postgres --dbname yiqiao_app /tmp/yiqiao-application.dump
docker compose -p "$RESTORE_PROJECT" exec -T postgres \
  rm -f /tmp/yiqiao-memory.dump /tmp/yiqiao-application.dump

docker compose -p "$RESTORE_PROJECT" create neo4j
ACTIVE_NEO4J_VOLUME="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$ACTIVE_PROJECT" \
  --filter label=com.docker.compose.volume=neo4j_data)"
RESTORE_NEO4J_VOLUME="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$RESTORE_PROJECT" \
  --filter label=com.docker.compose.volume=neo4j_data)"
test "$RESTORE_NEO4J_VOLUME" = "${RESTORE_PROJECT}_neo4j_data"
test "$(docker volume inspect --format \
  '{{index .Labels "com.docker.compose.project"}}' "$RESTORE_NEO4J_VOLUME")" = \
  "$RESTORE_PROJECT"
test "$(docker volume inspect --format \
  '{{index .Labels "com.docker.compose.volume"}}' "$RESTORE_NEO4J_VOLUME")" = \
  neo4j_data
if grep -Fqx -- "$RESTORE_NEO4J_VOLUME" "$PROTECTED_SOURCE_VOLUMES"; then
  echo "Refusing to use protected source volume $RESTORE_NEO4J_VOLUME" >&2
  exit 1
fi
if test -n "$ACTIVE_SERVER_DIR"; then
  test -n "$ACTIVE_NEO4J_VOLUME"
  test "$RESTORE_NEO4J_VOLUME" != "$ACTIVE_NEO4J_VOLUME"
fi
printf 'Restore Neo4j snapshot only to: %s\n' "$RESTORE_NEO4J_VOLUME"
```

With Neo4j still stopped, direct the host or storage-provider snapshot restore
at the exact volume printed in `RESTORE_NEO4J_VOLUME`; never enter or infer a
volume name manually. Continue in the same shell, confirm that the project label
still resolves to that volume, then restore history only into the replacement
checkout and start the replacement services:

```bash
test "$(docker volume ls -q \
  --filter "label=com.docker.compose.project=$RESTORE_PROJECT" \
  --filter label=com.docker.compose.volume=neo4j_data)" = "$RESTORE_NEO4J_VOLUME"
POSTGRES_CONTAINER="$(docker compose -p "$RESTORE_PROJECT" ps -aq postgres)"
NEO4J_CONTAINER="$(docker compose -p "$RESTORE_PROJECT" ps -aq neo4j)"
test -n "$POSTGRES_CONTAINER"
test -n "$NEO4J_CONTAINER"
RESOLVED_POSTGRES_VOLUME="$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' \
  "$POSTGRES_CONTAINER")"
RESOLVED_NEO4J_VOLUME="$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' \
  "$NEO4J_CONTAINER")"
test "$RESOLVED_POSTGRES_VOLUME" = "$RESTORE_VOLUME"
test "$RESOLVED_NEO4J_VOLUME" = "$RESTORE_NEO4J_VOLUME"
if grep -Fqx -- "$RESOLVED_POSTGRES_VOLUME" "$PROTECTED_SOURCE_VOLUMES" ||
   grep -Fqx -- "$RESOLVED_NEO4J_VOLUME" "$PROTECTED_SOURCE_VOLUMES"; then
  echo "Refusing to start services with a protected source volume" >&2
  exit 1
fi
test "$(pwd -P)" = "$RESTORE_SERVER_DIR"
test -z "$(find history -mindepth 1 -print -quit)"
tar --extract --gzip --file "$BACKUP_DIR/yiqiao-history.tar.gz" \
  --directory "$RESTORE_SERVER_DIR"
docker compose -p "$RESTORE_PROJECT" up -d neo4j yiqiao yiqiao-dashboard
```

The selective `.env` merge must already be complete before these services start.
The API applies only migrations newer than the restored database.

Verify health, login, API-key creation, add/search, export records, graph state,
and persistence after a full restart before cutover. `pg_restore --clean` and
volume replacement destroy existing data; the commands above are valid only for
the isolated replacement whose absolute target paths and volume names were
confirmed first.

## Upgrade

1. Read the release notes and migration changes.
2. Record image digests and create a complete backup.
3. Update the checkout with a fast-forward pull.
4. Compare the updated `server/.env.example` with the existing `server/.env`
   and add newly required non-secret settings manually. The initializer
   preserves an existing environment file and only fills missing deployment
   secrets; it does not merge arbitrary template keys.
5. Re-run the initializer to fill any missing required secrets and validate the
   resulting configuration.
6. Pull and recreate containers.
7. Validate health and the add/search smoke test.

```bash
git pull --ff-only
./scripts/init.sh
cd server
docker compose pull
docker compose up -d
docker compose ps
curl --fail http://localhost:8888/api/health
```

PowerShell uses
`powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1` from the
repository root. Database migrations run automatically when the API container
starts. Do not interrupt the first startup during migration.

## Rollback

Application rollback and data rollback are separate decisions. Re-point
`YIQIAO_API_IMAGE` and `YIQIAO_DASHBOARD_IMAGE` to the recorded prior tags or
digests only when the prior application is compatible with the migrated schema.
If a migration is not backward compatible, create a replacement deployment and
restore the pre-upgrade database and graph backups. Never attach an older binary
to the only copy of newly migrated data without reviewing the migration.

## Logs and Health

```bash
cd server
docker compose ps
docker compose logs --tail=200
docker compose logs --tail=200 yiqiao
curl --fail http://localhost:8888/api/health
```

Request logs may contain prompts, identifiers, and metadata. Limit access and
retention. Do not paste raw logs into public issues.

## Removal

Remove application containers but keep data:

```bash
cd server
docker compose down
```

Permanently remove containers and named database volumes:

This operation is never part of migration cleanup and must not be used for a
retained source, rollback volume, recovery clone, or evidence volume. Require a
successful isolated restore record, an exact approved-volume manifest, and a
protected-source inventory before deletion. The manifest contains one approved
volume name per line. Review the printed names out of band before setting the
confirmation value. Because `down -v` can also remove attached anonymous
volumes, this procedure enumerates actual container mounts and refuses deletion
when any volume lacks the reviewed Compose project and role labels:

```bash
set -euo pipefail
cd server
VERIFIED_BACKUP_RECORD=/secure/path/to/verified-restore-record.txt
APPROVED_DELETE_VOLUMES=/secure/path/to/approved-delete-volumes.txt
PROTECTED_SOURCE_VOLUMES=/secure/path/to/protected-source-volumes.txt

: "${REMOVE_PROJECT:?Set REMOVE_PROJECT to the exact reviewed project}"
test -s "$VERIFIED_BACKUP_RECORD"
test -s "$APPROVED_DELETE_VOLUMES"
test -f "$PROTECTED_SOURCE_VOLUMES"

mapfile -t REMOVE_CONTAINERS < <(docker compose -p "$REMOVE_PROJECT" ps -aq)
test "${#REMOVE_CONTAINERS[@]}" -gt 0
for CONTAINER in "${REMOVE_CONTAINERS[@]}"; do
  test "$(docker inspect --format \
    '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER")" = \
    "$REMOVE_PROJECT"
done

mapfile -t PROJECT_VOLUMES < <(docker volume ls -q \
  --filter "label=com.docker.compose.project=$REMOVE_PROJECT")
mapfile -t ATTACHED_VOLUMES < <(docker inspect --format \
  '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}' \
  "${REMOVE_CONTAINERS[@]}" | sed '/^$/d')
mapfile -t REMOVE_VOLUMES < <(printf '%s\n' \
  "${PROJECT_VOLUMES[@]}" "${ATTACHED_VOLUMES[@]}" | sed '/^$/d' | sort -u)
test "${#REMOVE_VOLUMES[@]}" -gt 0
printf 'Project selected for permanent volume deletion: %s\n' "$REMOVE_PROJECT"
printf '  %s\n' "${REMOVE_VOLUMES[@]}"
diff -u \
  <(sort -u "$APPROVED_DELETE_VOLUMES") \
  <(printf '%s\n' "${REMOVE_VOLUMES[@]}")

for VOLUME in "${REMOVE_VOLUMES[@]}"; do
  VOLUME_PROJECT="$(docker volume inspect --format \
    '{{index .Labels "com.docker.compose.project"}}' "$VOLUME")"
  VOLUME_ROLE="$(docker volume inspect --format \
    '{{index .Labels "com.docker.compose.volume"}}' "$VOLUME")"
  if test "$VOLUME_PROJECT" != "$REMOVE_PROJECT" || test -z "$VOLUME_ROLE"; then
    echo "Refusing down -v: $VOLUME is anonymous or lacks the reviewed project/role labels" >&2
    exit 1
  fi
  if grep -Fqx -- "$VOLUME" "$PROTECTED_SOURCE_VOLUMES"; then
    echo "Refusing to delete protected source volume $VOLUME" >&2
    exit 1
  fi
done

: "${DELETE_PROJECT_CONFIRMATION:?Set it only after reviewing the printed project and volumes}"
test "$DELETE_PROJECT_CONFIRMATION" = "delete:$REMOVE_PROJECT"
docker compose -p "$REMOVE_PROJECT" down -v
```

Then remove `server/history` and `server/.env` only after verifying the backup
and resolving the absolute paths. These files are not removed by Compose.

## Security Checklist

- Keep `AUTH_DISABLED=false`; the production overlay forces this value.
- Leave telemetry disabled unless the data path and recipient are approved.
- Keep API and dashboard binds on loopback or a private interface.
- Terminate TLS before any remote access.
- Rotate API keys, provider keys, database passwords, JWT secrets, and OAuth
  HMAC secrets after suspected exposure. Rotating an OAuth HMAC secret
  invalidates pending user codes or resets audit/rate-limit hash continuity.
- Treat database dumps, graph snapshots, history files, and request logs as
  sensitive user data.
- Apply dependency and base-image security updates and review the SBOM.
- Test restore and restart persistence regularly.
