# Branding Exceptions

YiQiao is the user-facing product name. This register documents inherited
identifiers that remain temporarily because changing them would break Python
imports, stored data, configuration, or integrations. An entry here is not
permission to add new upstream branding.

## 1. Python Import Namespace

The source directory and import namespace remain `mem0`:

```python
from mem0 import Memory, AsyncMemory
```

The API server and existing extensions import many modules below that namespace.
Renaming it would break callers, serialized type paths, plugins, and internal
imports in the current release. The installable distribution has been renamed
to `yiqiao-memory`; only the Python import path is retained.

Inherited class and variable identifiers derived from the namespace, such as
`Mem0ValidationError`, `_mem0_extract_entities`, and `mem0_client`, may remain in
internal code until a compatibility layer can be introduced. They must not be
shown as the product name in UI copy, API descriptions, or new documentation.

## 2. Configuration and Local State

The memory core and server still recognize legacy environment-variable prefixes
and local state paths so existing installations can migrate without losing
configuration. The retained surface includes:

- `MEM0_DIR`, `MEM0_API_KEY`, and the historical `~/.mem0` state directory.
- `scripts/import_chat_history.py` accepts `MEM0_BASE_URL`, `MEM0_USER_ID`, and
  `MEM0_AGENT_ID` after the corresponding `YIQIAO_*` variables. These CLI
  fallbacks let existing unattended import jobs migrate without silently
  changing their target or entity scope.
- `MEM0_TELEMETRY`, `MEM0_TELEMETRY_SAMPLE_RATE`, and
  `MEM0_TELEMETRY_STATE_PATH`. YiQiao's own telemetry setting takes precedence
  and is disabled by default.
- The `MEM0_LLM_*`, `MEM0_EMBEDDER_*`, `MEM0_EMBEDDING_*`,
  `MEM0_RERANK_*`, and `MEM0_DEFAULT_*` server configuration families.

New deployment-level settings should use a `YIQIAO_` prefix. Legacy aliases may
be removed only in a documented breaking release after the replacement has
shipped for at least one release and the migration guide covers unattended CLI
jobs as well as server configuration.

## 3. Persistent and Protocol Identifiers

Internal telemetry event names, migration collection names, deterministic UUID
namespaces, and stored configuration keys that contain `mem0` may remain where a
rename would split metrics, duplicate imported memories, or make existing data
unreadable. Telemetry is disabled by default. These identifiers are not a public
brand surface and new identifiers must use YiQiao naming.

The public vector-store configuration schemas in
`mem0/configs/vector_stores/*.py` retain inherited default collection,
database, table, keyspace, index, and namespace values such as `mem0` and
`mem0_db`. The matching persistent prefixes and implementation defaults remain
in `mem0/vector_stores/chroma.py`, `cassandra.py`, `databricks.py`,
`langchain.py`, `neptune_analytics.py`, `redis.py`, and `valkey.py`. Existing
library users may have data stored only under those implicit locators; changing
them in place would make that data appear missing or create a second store.
YiQiao's server config supplies its own `memories` collection for new standard
deployments, and new custom integrations should choose an explicit
YiQiao-neutral locator. These inherited defaults can be removed only in a major
release that detects legacy stores, provides a tested copy-or-rename migration,
and supports rollback or dual-read during the transition.

Existing protocol headers or SDK fields required by a third-party compatibility
API may remain only on those compatibility routes. YiQiao-native endpoints,
webhooks, request headers, and examples must use YiQiao-neutral or standard
names.

## 4. Legal and Historical Attribution

References to Mem0 and `mem0ai/mem0` are retained in `LICENSE`, `NOTICE`, this
file, third-party notices, source history, and clearly labeled upstream
attribution. These references identify the source of the derivative work and do
not imply sponsorship or endorsement.

## 5. Secret-Scanning Configuration

YiQiao's public release history is a clean source snapshot and does not retain
the upstream repository's commit-qualified secret-scanning fingerprints. There
is no `.gitleaksignore` in the release tree; both the current tree and every
public YiQiao commit must pass without fingerprint exceptions.

`.gitleaks.toml` allows a match only when the detected text is
exactly the adjacent empty `OPENAI_API_KEY=` and `ANTHROPIC_API_KEY=`
entries. It does not allowlist a path, so the rest of the environment template
is still scanned by every rule; populating either value makes this expression
stop matching. The configuration contains no secret values, code excerpts,
author names, email addresses, or product copy.

## Not Exceptions

The following are not compatibility requirements and must not appear in the
YiQiao release experience:

- Upstream cloud sign-up, dashboard, MCP, documentation, demo, Discord, social,
  support-email, package-download, fundraising, or marketing links.
- Upstream logos, badges, screenshots, analytics keys, or hosted-service calls.
- Upstream product names in page titles, navigation, API summaries, image names,
  Compose services, default labels, or new examples.
- Publishing YiQiao artifacts under upstream package or organization names.

Any new exception requires a concrete compatibility reason, affected files or
interfaces, a removal condition, and maintainer review.
