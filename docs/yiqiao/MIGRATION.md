# Migrating to YiQiao

[简体中文](MIGRATION.zh-CN.md) | **English**

This guide covers migration from an earlier self-hosted development build or an
upstream-derived Compose installation. It does not provide an automatic hosted
service export. Use the dashboard chat-history importer or a supported export
workflow for data that is not already in the local PostgreSQL, Neo4j, and
history stores.

## Before Migrating

Record:

- The source commit or image digests.
- Compose project, service, network, and volume names.
- PostgreSQL user and both database names.
- Whether Neo4j graph memory is enabled.
- Public URLs, reverse-proxy configuration, provider routes, and embedding
  dimensions.
- Administrator, project, API-key, retention, quota, webhook, and import state.

Create and test a complete backup using [Operations](OPERATIONS.md). Do not
continue with the only copy of the source volumes.

## Configuration Migration

Run the YiQiao initializer to create a new environment file. Do not copy an old
`.env` wholesale; that can reintroduce weak secrets, public database bindings,
disabled authentication, stale image names, or obsolete upstream endpoints.

```bash
git clone https://github.com/daizhongming/YiQiao.git
cd YiQiao
./scripts/init.sh
```

On PowerShell use
`powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1`. Compare the old
and new files manually, carrying forward only required provider and deployment
values. Keep the newly generated database and JWT secrets unless the existing
databases require their current credentials.

Set `PUBLIC_API_URL` and `PUBLIC_DASHBOARD_URL` when deploying behind a reverse
proxy. Keep authentication enabled and telemetry disabled during migration.

For the embedded Python memory core, a fresh home directory uses `~/.yiqiao`.
When neither `YIQIAO_DIR` nor the legacy override is set, YiQiao continues using
an existing `~/.mem0` only if `~/.yiqiao` does not yet exist. This compatibility
fallback avoids hiding existing `history.db` and `config.json`; it does not copy
or delete either directory. Set `YIQIAO_DIR` explicitly after validating any
planned state move.

Legacy core environment prefixes remain accepted for compatibility. New
deployment settings use YiQiao names.

## OAuth Device Flow Retirement

Alembic revision `019` removes OAuth Device Flow, all public connector endpoints, and the Connected Apps dashboard. Applying the migration permanently deletes OAuth application registrations, pending device codes, grants, refresh tokens, and OAuth audit rows. Existing OAuth credentials stop working immediately.

Before upgrading, migrate integrations to project-scoped API keys. Send the key through `X-API-Key` and select the project with `X-Project-ID`. Migration `019` is intentionally irreversible because deleted credentials cannot be restored safely.

## Data Migration Options

### Logical Restore to New Volumes

This is the preferred path because it preserves the source deployment and makes
rollback straightforward:

1. Initialize an isolated YiQiao replacement with new, empty volumes, but do not
   start the API, dashboard, or Neo4j.
2. Follow the empty-database restore procedure in
   [Operations](OPERATIONS.md#restore) for `postgres` and `yiqiao_app`.
3. Restore a consistent Neo4j snapshot into the new `neo4j_data` volume while
   Neo4j is stopped.
4. Copy the required memory-history SQLite file, import workspaces, and retained
   import sources into `server/history`.
5. Restore the required secrets, start the stack, and allow only migrations
   newer than the restored database to complete.

### Reuse Cloned Volumes

Treat every original source volume and bind-mounted data directory as read-only
evidence. Never attach one to a YiQiao target service or start a database engine
against it. Reuse is permitted only from a quiescent snapshot or verified clone;
it is faster than logical restore but has a larger rollback risk. Docker Compose
volume names are prefixed by the project name, so an old `server_postgres_db` or
similarly named volume is not automatically the new `yiqiao-v3_postgres_db`
volume. Inspect labels and mount points with `docker volume inspect` before
changing anything.

Reuse a cloned volume only after confirming that the source data directory uses
the same PostgreSQL major version as the target image, that its pgvector
extension is compatible, and that the Neo4j store format is supported by the
target Neo4j version. The current YiQiao PostgreSQL image uses PostgreSQL 17; a
PostgreSQL 15 or 16 data directory cannot be mounted directly. If any version is
different or unknown, use the preferred logical dump and restore path instead.

Do not rename or delete the source volume. Create a snapshot or cloned volume,
then point a reviewed Compose override at the clone. Never attach both old and
new database containers to the same writable data directory.

Before creating the replacement, record each source volume's exact name,
mountpoint, Compose project and volume labels, storage snapshot identifier, and
content checksum or backup seal. Give the replacement a unique Compose project
and record the expected clone names and labels separately. Source volumes remain
stopped and protected for the entire migration and acceptance period.

After creating the replacement database containers, but before starting the API
or allowing any migration to run, inspect the resolved container mounts. This is
a hard gate, not a visual spot check:

- The mount source and volume name for PostgreSQL and Neo4j must exactly match
  the registered replacement clone or new restore volume.
- The resolved name, mountpoint, Compose project label, and Compose volume label
  must not match any registered source entry.
- The replacement project label must equal the unique migration project, and
  the volume labels must identify the expected `postgres_db` and `neo4j_data`
  roles.
- No target Compose file, rendered configuration, or created container may
  reference an original source name or mountpoint.

Capture the rendered Compose configuration, `docker inspect` mount results, and
`docker volume inspect` label results as migration evidence. Any missing label,
unknown mount, version mismatch, or source match is **NO-GO**. Correct the clone
or override and repeat the gate; never make the source writable to get past it.

### Selective Legacy Request-Log Merge

The full restore procedure is not a merge procedure. Use this section only when
selected legacy operational records must be added to an already populated
YiQiao target. Perform every preflight and rehearsal against isolated clones.
Do not write to the live target until the rehearsal has passed and a new target
snapshot has been verified.

`request_logs` rows are operational audit records. They are not memories,
embedding vectors, Qdrant points, Neo4j nodes, or graph relationships. Never
report a request-log count as a memory count, and never infer memory recovery
from a successful request-log import.

Use the following protocol:

1. Seal logical exports from each source and take a consistent target snapshot
   covering both PostgreSQL databases, Neo4j, history, and required secrets.
   Prove that the target snapshot restores in isolation before any merge write.
2. Restore each PostgreSQL source under its original major version in a
   network-isolated rehearsal. Export only the reviewed tables and fields; do
   not attach a PostgreSQL 15 or 16 data directory to PostgreSQL 17.
3. Inventory target and source users using a documented stable identity key,
   such as normalized email, while keeping raw identity data restricted. If a
   stable identity maps to different user IDs or roles, especially an
   administrator/member mismatch, stop with **NO-GO**. An operator must approve
   an explicit role policy and a new rehearsal; the importer must never silently
   promote, downgrade, duplicate, or skip the identity.
4. Match each request row by UUID and by a canonical fingerprint of the reviewed
   source-to-target field mapping. A reused UUID with a different fingerprint is
   a conflict and therefore **NO-GO**. Do not add source counts together until
   overlap and conflicts have been measured.
5. Insert only proven-missing rows. Record every inserted row and any identity
   created by the migration in an immutable, run-specific owned-row ledger that
   includes the source seal and canonical fingerprint. Preserve a separate
   fingerprint and count for all pre-existing target rows.
6. Run the exact import twice against the isolated target clone. The second run
   must insert, update, and delete zero rows; source-missing and conflicting-row
   counts must both be zero, and the pre-existing target fingerprint must remain
   unchanged.
7. Rehearse selective rollback by deleting only ledger-owned rows and identities,
   then prove that the target counts and fingerprints return to the snapshot
   baseline. If ownership cannot be proven, discard the rehearsal clone and use
   the verified target snapshot instead of attempting a partial rollback.

Do not activate legacy API keys, refresh-token records, sessions, or other
credentials as part of a request-log merge. Preserve unsupported tables in the
sealed archive until a separate schema and security policy has been reviewed.

Inventory legacy Neo4j graphs and Qdrant collections independently, including
exact counts, versions, vector dimensions, collection names, and full-scroll or
store-check evidence. When the relevant graph or vector dataset has zero records,
the required action is **NO-OP**: do not create an empty target, restore its
physical store, or treat unrelated routing/configuration points as memories.
Non-empty graph or vector data requires its own reviewed migration and identity
mapping; it is outside the request-log merge.

## Embedding Compatibility

Keep the original embedding provider, model, and vector dimensions for the first
migration start. Changing dimensions while reusing a vector collection can make
existing vectors unreadable or cause write failures. Move to a new collection
and re-embed through an explicit migration if the model or dimensions must
change.

## First Start

Complete the resolved-mount gate above before these commands. Run them only from
the isolated replacement checkout with its unique Compose project. If the API
would resolve either database mount to a registered source volume or directory,
stop with **NO-GO**; starting the API can apply irreversible migrations.

```bash
cd server
: "${MIGRATION_PROJECT:?Set MIGRATION_PROJECT to the reviewed unique project}"
docker compose -p "$MIGRATION_PROJECT" up -d postgres neo4j
docker compose -p "$MIGRATION_PROJECT" ps
docker compose -p "$MIGRATION_PROJECT" up -d yiqiao yiqiao-dashboard
docker compose -p "$MIGRATION_PROJECT" logs --tail=200 yiqiao
```

The API applies Alembic migrations automatically. A migration failure is a hard
stop: preserve the logs privately, stop the new stack, and investigate before
retrying. Do not mark a failed migration as complete or bypass its revision.

## Validation

Validate all of the following before directing clients to YiQiao:

- API and dashboard health endpoints succeed.
- The existing administrator can sign in, or a new administrator can be created
  only when no user exists.
- Existing projects, API keys, memories, settings, webhooks, quotas, requests,
  and exports appear in the correct scope.
- Existing memory search returns expected results.
- A new memory can be added and found with a newly created project API key.
- Graph status is healthy and known relationships are visible.
- Imports can access retained source/workspace files, server-side export records
  and results are present in the application database, and a new client download
  succeeds.
- After a full Compose restart, the old and new validation records remain.

Keep the source deployment stopped but intact until this validation and an
operator acceptance period have completed.

## Client Cutover

Change clients to the YiQiao API URL and issue new project API keys when
possible. The self-hosted REST compatibility routes remain available in the
initial release, but upstream hosted-service defaults, credentials, and support
links are not part of YiQiao.

Verify reverse-proxy request size and timeout settings before large chat-history
imports. Update webhook receivers to allow the YiQiao origin and validate the
current signing header and secret using a test delivery.

## Rollback

Before cutover, rollback is simply stopping the new deployment and restarting
the unchanged source deployment. After clients write to YiQiao, rollback can
lose or fork data. Stop writes, export the delta if supported, and make an
explicit reconciliation decision.

If the source schema cannot read migrated databases, restore its pre-migration
backups into separate volumes. Do not start an older API against the migrated
production volumes.

Migration `019` cannot restore removed OAuth credentials. Roll back by
restoring a verified pre-upgrade database backup, then migrate integrations to
project API keys before upgrading again.

For a selective request-log merge, stop target writes and use the owned-row
ledger to remove only rows created by that merge. Verify the pre-existing target
fingerprints after rollback. If any row has ambiguous ownership or the baseline
cannot be reproduced exactly, restore the verified target snapshot into a new
replacement deployment and reconcile explicitly. Never roll back by swapping an
original legacy volume into the target.

## Legal Notices

For licensing, third-party attribution, and the record of modifications, see
[NOTICE](../../NOTICE), [Third-Party Notices](../../THIRD_PARTY_NOTICES.md), and
[Modification Notices](../../MODIFICATIONS.md).
