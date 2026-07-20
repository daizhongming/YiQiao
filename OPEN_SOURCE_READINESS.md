# YiQiao Open-Source Readiness

Last updated: 2026-07-20

This document defines the release gates for YiQiao and records the design of
the first public source release. GitHub Actions results for the exact commit are
the authoritative remote evidence; local results alone do not replace them.

## Release Scope

The public repository contains the YiQiao memory API, operations dashboard,
Python compatibility core, database migrations, Docker Compose deployment,
initialization and maintenance scripts, tests, and project documentation. It
must not contain local environment files, runtime history, logs, browser output,
database dumps, backups, caches, credentials, or user data.

YiQiao is an independent derivative of the Mem0 open-source project. It is not
an official Mem0 product and does not claim exclusive ownership of inherited
code. The release preserves the Apache License 2.0, upstream copyright notices,
the upstream source reference, third-party notices, and the audited list of
modified upstream files in `MODIFICATIONS.md`.

## Public History

The first YiQiao release is added as a source snapshot on top of the target
repository's original placeholder commit. It is not merged, rebased, or
cherry-picked from the upstream or review-branch history. This keeps the public
YiQiao history focused on this product while `NOTICE` and `MODIFICATIONS.md`
preserve the required provenance of the code itself.

The public release has no `.gitleaksignore`. Gitleaks must report zero findings
for both the complete public history and the checked-out tree. The narrow
`.gitleaks.toml` rule only accepts adjacent empty provider-key placeholders in
the committed environment template.

## Required Gates

| Area | Required evidence |
| --- | --- |
| Source identity | Only files from the exact Git index; no copied release, smoke, backup, or runtime directories |
| Licensing | Apache modification-notice audit passes and all legal files are present in source, wheel, and images |
| Secrets | Gitleaks current-tree and complete-history scans report zero findings without fingerprint exceptions |
| Python | Formatting, lint, compilation, core tests, provider-isolation tests, and release legal tests pass |
| Dashboard | Frozen install, Prettier, ESLint with zero warnings, TypeScript, unit tests, and production build pass |
| Dependencies | Python and dashboard dependency checks report no known actionable vulnerabilities |
| Compose | Base, source-build, production, release, and E2E configurations parse successfully |
| Images | API and dashboard build for supported architectures; published images include provenance and SBOMs |
| Runtime | Initialization, health, administrator setup, API key, memory add/search, restart, and persistence pass |
| UX | Desktop and mobile login, requests, memory, and graph views render without console errors or overflow |
| Documentation | Internal links, literal Quick Start commands, branding, security, operations, and rollback guidance pass review |

The four repository workflows are intentionally separate:

- `YiQiao CI` validates source, Python, Dashboard, Compose, and packaging.
- `YiQiao Security` scans secrets and dependencies.
- `YiQiao Full Stack` exercises a clean source-built deployment.
- `YiQiao Images` verifies multi-architecture images on pushes and publishes
  only after an explicit default-branch dispatch with `publish=true`.

## Reproducible Local Checks

Run the checks relevant to a change before pushing:

```bash
python scripts/audit_modification_notices.py --fetch-base
python -m pytest -q tests/test_release_legal_payload.py
python -m pytest -q tests/
ruff format --check .
ruff check .
gitleaks dir --redact=100 --no-banner .
gitleaks git --redact=100 --no-banner .
```

```bash
cd server/dashboard
pnpm install --frozen-lockfile
pnpm run format:check
pnpm run lint
pnpm run typecheck
pnpm run test:unit
pnpm run build
pnpm audit --audit-level high
```

```bash
cd server
docker compose config --quiet
docker compose -f docker-compose.yaml -f docker-compose.build.yaml config --quiet
docker compose -f docker-compose.yaml -f docker-compose.production.yaml config --quiet
```

Use `python scripts/full_stack_smoke.py` for an isolated source-build runtime
test. It creates a unique Compose project and must not reuse production data.

## Publication Checklist

1. Require an empty worktree and verify that the committed tree matches the
   reviewed index export.
2. Push the snapshot to `main` with an ordinary fast-forward update. Never use
   force-push for release publication.
3. Require all four workflows to succeed on the exact `main` commit.
4. Dispatch `YiQiao Images` from `main` with `publish=true`; verify both GHCR
   packages are public and anonymously resolve `latest` and commit-SHA tags.
5. Run the literal README clone, initializer, default Compose startup, health,
   setup, memory add, and search path from a clean checkout.
6. Remove obsolete public review refs so upstream history is not reachable
   through a YiQiao branch, then repeat the all-ref secret and path audit.
7. Record immutable image digests before announcing or upgrading a deployment.

## Residual Risks

- Optional provider and vector-store adapters depend on external services,
  licenses, credentials, rate limits, privacy terms, and data residency choices
  controlled by the operator.
- The default Compose deployment is single-host and not highly available.
- Neo4j Community lacks enterprise clustering and online-backup features; a
  consistent graph backup requires a maintenance window or storage snapshot.
- Provider secrets configured in the dashboard reside in PostgreSQL. Operators
  must protect database backups, administrator sessions, and host access.
- Version `0.1.x` is an initial open-source line; operators should review API,
  schema, provider, and image compatibility before every upgrade.

## Rollback

Revert source changes with an ordinary reviewed commit. Do not reset or rewrite
`main`. For deployed systems, record the current image digests and data state,
then use a prior image only when it is compatible with the migrated schema. A
backward-incompatible migration requires an isolated replacement and restoration
of the corresponding PostgreSQL, Neo4j, history, and encrypted configuration
backups. The complete procedures are in `docs/yiqiao/OPERATIONS.md`.
