# Documentation Language Coverage

[简体中文](DOCUMENTATION_COVERAGE.zh-CN.md) | **English**

Last reviewed: 2026-07-27

This inventory is the authoritative scope for YiQiao's public English and
Simplified Chinese documentation. English sources remain normative. A `pass`
validation value is valid only when `make docs-check` succeeds on the same
commit.

Kinds and policies:

- `markdown`: reciprocal language links and exact shell-family fenced blocks
  are required as recorded below.
- `legal`: the English payload remains byte-stable; the Chinese reference must
  link to it and carry the unofficial-reference disclaimer.
- `issue-form`: GitHub Issue Form pairs must preserve required fields and link
  to one another.
- `env`: executable assignments must be identical; only comments are localized.

<!-- docs-localization:pairs:start -->
| English source | Simplified Chinese | Kind | Reciprocal | Shell blocks | Translation | Validation |
| --- | --- | --- | --- | --- | --- | --- |
| `README.md` | `README.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `CONTRIBUTING.md` | `CONTRIBUTING.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `SECURITY.md` | `SECURITY.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `CODE_OF_CONDUCT.md` | `CODE_OF_CONDUCT.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `OPEN_SOURCE_READINESS.md` | `OPEN_SOURCE_READINESS.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `BRANDING_EXCEPTIONS.md` | `BRANDING_EXCEPTIONS.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `MODIFICATIONS.md` | `MODIFICATIONS.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `THIRD_PARTY_NOTICES.md` | `THIRD_PARTY_NOTICES.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `LICENSE` | `LICENSE.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `NOTICE` | `NOTICE.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `docs/yiqiao/README.md` | `docs/yiqiao/README.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/LEGAL.md` | `docs/yiqiao/LEGAL.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/MIGRATION.md` | `docs/yiqiao/MIGRATION.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/OPERATIONS.md` | `docs/yiqiao/OPERATIONS.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/PUBLIC_CONNECTOR.md` | `docs/yiqiao/PUBLIC_CONNECTOR.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/RELEASE_0.2.0.md` | `docs/yiqiao/RELEASE_0.2.0.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/RELEASE_0.2.1.md` | `docs/yiqiao/RELEASE_0.2.1.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/SECURITY_AUDIT.md` | `docs/yiqiao/SECURITY_AUDIT.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/TROUBLESHOOTING.md` | `docs/yiqiao/TROUBLESHOOTING.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `docs/yiqiao/DOCUMENTATION_COVERAGE.md` | `docs/yiqiao/DOCUMENTATION_COVERAGE.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `server/README.md` | `server/README.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `server/dashboard/public/fonts/FONT_LICENSES.md` | `server/dashboard/public/fonts/FONT_LICENSES.zh-CN.md` | `legal` | `legal-exception` | `not-applicable` | `complete` | `pass` |
| `.github/PULL_REQUEST_TEMPLATE.md` | `.github/PULL_REQUEST_TEMPLATE.zh-CN.md` | `markdown` | `required` | `required` | `complete` | `pass` |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | `.github/ISSUE_TEMPLATE/bug_report.zh-CN.yml` | `issue-form` | `required` | `not-applicable` | `complete` | `pass` |
| `.github/ISSUE_TEMPLATE/documentation_issue.yml` | `.github/ISSUE_TEMPLATE/documentation_issue.zh-CN.yml` | `issue-form` | `required` | `not-applicable` | `complete` | `pass` |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | `.github/ISSUE_TEMPLATE/feature_request.zh-CN.yml` | `issue-form` | `required` | `not-applicable` | `complete` | `pass` |
| `server/.env.example` | `server/.env.example.zh-CN` | `env` | `required` | `not-applicable` | `complete` | `pass` |
<!-- docs-localization:pairs:end -->

The following paths are intentionally outside the paired-document rule. Exact
paths are checked for existence; patterns define durable scanner boundaries.

<!-- docs-localization:exclusions:start -->
| Path or pattern | Reason |
| --- | --- |
| `.github/ISSUE_TEMPLATE/config.yml` | GitHub supports one reserved chooser configuration; its visible text is bilingual inline. |
| `server/dashboard/public/fonts/OFL-1.1.txt` | Unchanged third-party SIL OFL text must remain verbatim. |
| `server/dashboard/.env.example` | Dashboard build-time template; operator configuration is documented in the paired server environment template. |
| `server/requirements.txt` | Machine-consumed dependency manifest. |
| `server/dashboard/pnpm-lock.yaml` | Generated dependency lockfile. |
| `.github/workflows/**` | CI configuration rather than user documentation. |
| `scripts/**` | Executable maintenance source rather than user documentation. |
| `tests/**` | Test source and fixtures rather than user documentation. |
| `mem0/**` | Internal compatibility implementation source. |
| `yiqiao/**` | Python implementation source. |
| `server/**/*.py` | Server implementation source. |
| `server/dashboard/src/**` | Dashboard implementation and UI source. |
| `server/docker-compose*.yaml` | Machine-consumed deployment configuration. |
| `server/**/Dockerfile*` | Container build configuration. |
| `Makefile` | Build automation. |
| `pyproject.toml` | Python build and tool configuration. |
| `**/node_modules/**` | Installed dependencies. |
| `**/.next/**` | Generated Dashboard build output. |
| `**/build/**` | Generated build output. |
| `**/dist/**` | Generated distribution output. |
| `**/coverage/**` | Generated coverage output. |
| `**/htmlcov/**` | Generated HTML coverage output. |
| `**/history/**` | Runtime history and imported user data. |
| `**/logs/**` | Runtime logs. |
| `**/backups/**` | Operator backups. |
| `**/artifacts/**` | Generated test or release artifacts. |
| `**/playwright-report/**` | Generated browser-test report. |
| `**/test-results/**` | Generated test output. |
| `**/__pycache__/**` | Generated Python bytecode cache. |
<!-- docs-localization:exclusions:end -->

Run `make docs-check` before submitting documentation changes. The checker uses
this inventory, validates every source and target, discovers unlisted English
Markdown and MDX files, and fails on stale records.
