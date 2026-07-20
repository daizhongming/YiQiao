# Contributing to YiQiao
> **Modification notice:** This file was modified in 2026 by YiQiao contributors. See NOTICE.

YiQiao accepts focused bug fixes, security hardening, documentation, tests, and
features that improve the self-hosted API, dashboard, memory core, migrations,
or operating experience.

## Before Starting

Search [existing issues](https://github.com/daizhongming/YiQiao/issues). Open an
issue before a large behavior, schema, dependency, or architecture change so the
scope and migration path can be agreed before implementation. Security reports
must follow [SECURITY.md](SECURITY.md), not the public issue tracker.

By participating, you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md). Contributions are submitted under the
repository's Apache License 2.0 unless explicitly stated otherwise.

## Repository Scope

| Area               | Path                                      | Primary checks                               |
| ------------------ | ----------------------------------------- | -------------------------------------------- |
| Memory core        | `mem0/`                                   | Ruff, compile, pytest                        |
| API and migrations | `server/`, `server/alembic/`              | Ruff, compile, pytest, Compose               |
| Dashboard          | `server/dashboard/`                       | Prettier, TypeScript, Next.js build          |
| Deployment         | `server/docker-compose*.yaml`, `scripts/` | Compose config, clean build, smoke test      |
| Release docs       | root Markdown, `docs/yiqiao/`             | links, commands, branding and license review |

The Python distribution is named `yiqiao-memory`, while the inherited `mem0`
import namespace remains a compatibility interface. Do not rename compatibility
identifiers or add new uses of the upstream brand without updating
[BRANDING_EXCEPTIONS.md](BRANDING_EXCEPTIONS.md) and providing a migration plan.

## Development Setup

Initialize the local Compose configuration:

```bash
git clone https://github.com/daizhongming/YiQiao.git
cd YiQiao
./scripts/init.sh
```

On Windows PowerShell, use
`powershell -ExecutionPolicy Bypass -File .\scripts\init.ps1` instead. The
scripts do not replace an existing `server/.env`.

For Python development:

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test,dev]"
python -m pip install -r server/requirements.txt
```

For dashboard development:

```bash
cd server/dashboard
corepack enable
pnpm install --frozen-lockfile
```

## Required Checks

Run the checks for every area you change. At minimum:

```bash
python -m isort --check-only --profile black mem0 server tests scripts
python -m ruff format --check mem0 server tests scripts
python -m ruff check mem0 server tests scripts
python -m compileall -q mem0 server scripts
python scripts/audit_modification_notices.py --fetch-base
python -m pytest -q tests \
  --ignore=tests/embeddings \
  --ignore=tests/llms \
  --ignore=tests/rerankers \
  --ignore=tests/vector_stores
```

Provider-adapter changes require the larger optional environment and its
separate suite:

```bash
python -m pip install -e ".[test,vector-stores,llms,extras,nlp]"
python -m pytest -q \
  tests/embeddings tests/llms tests/rerankers tests/vector_stores
```

Dashboard changes:

```bash
cd server/dashboard
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test:unit
pnpm build
```

Deployment changes:

```bash
cd server
docker compose config --quiet
docker compose -f docker-compose.yaml -f docker-compose.production.yaml config --quiet
docker compose -f docker-compose.yaml -f docker-compose.build.yaml build
```

Tests must not read a developer's `server/.env`, write to real YiQiao volumes,
call paid providers without an explicit integration marker, or depend on prior
local containers. Add a migration for every schema change and test both a fresh
database and upgrade behavior.

## Pull Requests

1. Branch from the current default or designated release branch.
2. Keep one logical change per pull request.
3. Add regression tests and user or operator documentation for changed behavior.
4. Use a Conventional Commit subject such as `fix:`, `feat:`, `docs:`, or
   `test:`.
5. Link the issue with `Closes #<number>` when applicable.
6. Complete the pull request template and report the exact validation commands.
7. Call out migrations, compatibility changes, new network access, secrets,
   telemetry, dependencies, and license implications explicitly.

Do not commit `.env` files, credentials, database contents, history, logs,
screenshots containing user data, Playwright output, build output, or local
backups. Run a secret scan before pushing.

## Documentation and Branding

Use **YiQiao** for the product, UI, images, services, examples, and new public
interfaces. Legal upstream attribution belongs in `NOTICE`,
`THIRD_PARTY_NOTICES.md`, and narrowly labeled attribution sections. Do not add
links to an upstream hosted product, support channel, social account, or release
pipeline.

When adding or updating a dependency:

- Prefer a maintained dependency already used by the relevant component.
- Pin or constrain it consistently with the existing lockfile strategy.
- Review known vulnerabilities and transitive changes.
- Record its license and distribution obligations in
  `THIRD_PARTY_NOTICES.md` when it enters a shipped artifact.

## Review Expectations

Maintainers may request smaller commits, additional tests, a migration or
rollback plan, security changes, or license clarification. Approval does not
guarantee an immediate release. Releases are created only by maintainers after
all repository gates pass.
