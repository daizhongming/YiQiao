# Third-Party Notices

YiQiao uses third-party open-source software. This inventory covers direct
runtime dependencies, direct dashboard build dependencies, and the container
images with material redistribution requirements as reviewed on 2026-08-05.
The version constraints and resolved versions in `pyproject.toml`,
`server/requirements.txt`, `yiqiao-mcp/pyproject.toml`,
`server/dashboard/package.json`, and `server/dashboard/pnpm-lock.yaml` are
authoritative for a particular build.

YiQiao's Apache License 2.0 does not replace the license of any dependency.
Copyright notices and license files shipped by dependencies must remain in
binary distributions. This file is informational and is not legal advice.

## Derived Source

YiQiao contains modified source from the Mem0 open-source project
(<https://github.com/mem0ai/mem0>), licensed under Apache-2.0. See [NOTICE](NOTICE)
and [LICENSE](LICENSE).

## Python Runtime

| Dependency                | License                    | Project source                                                     |
| ------------------------- | -------------------------- | ------------------------------------------------------------------ |
| `qdrant-client`           | Apache-2.0                 | <https://github.com/qdrant/qdrant-client>                          |
| `pydantic`                | MIT                        | <https://github.com/pydantic/pydantic>                             |
| `openai`                  | Apache-2.0                 | <https://github.com/openai/openai-python>                          |
| `httpx`                   | BSD-3-Clause               | <https://github.com/encode/httpx>                                  |
| `posthog`                 | MIT                        | <https://github.com/PostHog/posthog-python>                        |
| `pytz`                    | MIT                        | <https://github.com/stub42/pytz>                                   |
| `SQLAlchemy`              | MIT                        | <https://github.com/sqlalchemy/sqlalchemy>                         |
| `protobuf`                | BSD-3-Clause               | <https://github.com/protocolbuffers/protobuf>                      |
| `fastapi`                 | MIT                        | <https://github.com/fastapi/fastapi>                               |
| `starlette`               | BSD-3-Clause               | <https://github.com/Kludex/starlette>                              |
| `uvicorn`                 | BSD-3-Clause               | <https://github.com/encode/uvicorn>                                |
| `python-multipart`        | Apache-2.0                 | <https://github.com/Kludex/python-multipart>                       |
| `psycopg`, `psycopg-pool` | LGPL-3.0-only              | <https://github.com/psycopg/psycopg>                               |
| `neo4j` Python driver     | Apache-2.0                 | <https://github.com/neo4j/neo4j-python-driver>                     |
| `spacy`                   | MIT                        | <https://github.com/explosion/spaCy>                               |
| `anthropic`               | MIT                        | <https://github.com/anthropics/anthropic-sdk-python>               |
| `google-generativeai`     | Apache-2.0                 | <https://github.com/google-gemini/deprecated-generative-ai-python> |
| `alembic`                 | MIT                        | <https://github.com/sqlalchemy/alembic>                            |
| `passlib`                 | BSD                        | <https://foss.heptapod.net/python-libs/passlib>                    |
| `bcrypt`                  | Apache-2.0                 | <https://github.com/pyca/bcrypt>                                   |
| `PyJWT`                   | MIT                        | <https://github.com/jpadilla/pyjwt>                                |
| `cryptography`            | Apache-2.0 OR BSD-3-Clause | <https://github.com/pyca/cryptography>                             |
| `slowapi`                 | MIT                        | <https://github.com/laurentS/slowapi>                              |

Optional Python provider and vector-store extras are not installed in the
default API image unless selected in a custom build. Their licenses must be
reviewed before distributing that custom image.

## MCP Companion Runtime

The independent `yiqiao-mcp` distribution declares the following direct
runtime dependencies. Dependencies already used by the API are repeated here
so the companion inventory remains complete when it is distributed alone.

| Dependency   | License      | Project source                                             |
| ------------ | ------------ | ---------------------------------------------------------- |
| `anyio`      | MIT          | <https://github.com/agronholm/anyio>                       |
| `httpx`      | BSD-3-Clause | <https://github.com/encode/httpx>                          |
| `jsonschema` | MIT          | <https://github.com/python-jsonschema/jsonschema>          |
| `mcp`        | MIT          | <https://github.com/modelcontextprotocol/python-sdk>       |
| `pydantic`   | MIT          | <https://github.com/pydantic/pydantic>                     |
| `starlette`  | BSD-3-Clause | <https://github.com/Kludex/starlette>                      |
| `uvicorn`    | BSD-3-Clause | <https://github.com/encode/uvicorn>                        |

The companion uses `hatchling` (MIT) to build distributions. Its optional
test dependencies are `pytest` (MIT) and `pytest-asyncio` (Apache-2.0).
These build and test packages are not installed in the runtime container.

## Dashboard Runtime

The following direct runtime packages are MIT licensed:

`@hookform/resolvers`, all direct `@radix-ui/react-*` packages,
`@reduxjs/toolkit`, `@tanstack/react-table`, `axios`, `clsx`, `cmdk`, `date-fns`,
`framer-motion`, `lodash`, `next`, `next-themes`, `react`,
`react-copy-to-clipboard`, `react-day-picker`, `react-dom`,
`react-force-graph-2d`, `react-hook-form`, `react-redux`,
`react-syntax-highlighter`, `recharts`, `redux`, `sonner`, `tailwind-merge`,
`tailwindcss-animate`, `use-debounce`, `uuid`, `vaul`, and `zod`.

Additional direct runtime licenses:

| Dependency                 | License    | Project source                           |
| -------------------------- | ---------- | ---------------------------------------- |
| `class-variance-authority` | Apache-2.0 | <https://github.com/joe-bell/cva>        |
| `lucide-react`             | ISC        | <https://github.com/lucide-icons/lucide> |

The build-time and test packages `@tailwindcss/typography`,
`@testing-library/react`, the direct `@types/*` packages, `autoprefixer`,
`eslint`, `eslint-config-next`, `jsdom`, `postcss`, `prettier`, `tailwindcss`,
and `vitest` are MIT licensed. `typescript` is Apache-2.0 licensed.

The dashboard lockfile includes transitive dependencies in addition to this
direct inventory. Their package metadata and bundled license files are the
authoritative notices for the resolved build. The published dashboard image
stores the production-license inventory and collected package metadata and
license files under `/usr/share/licenses/yiqiao/npm/`.

## Dashboard Fonts

The dashboard bundles Inter 4.001, Fustat 1.010, DM Mono 1.000, and Roboto Mono
3.000 font binaries inherited unchanged from the upstream Mem0 import. Inter,
Fustat, and DM Mono are licensed under the SIL Open Font License 1.1; Roboto Mono
is licensed under Apache-2.0. The reviewed file hashes, embedded copyright
statements, versions, fixed historical license source, and full OFL text are in
the [bundled font notice](server/dashboard/public/fonts/FONT_LICENSES.md).
The dashboard image copies these notices under
`/usr/share/licenses/yiqiao/fonts/` in addition to the browser asset directory.

## Container Images

| Image family             | Material licenses                                                                          | Distribution note                                                                                                                                                                                                                 |
| ------------------------ | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python:3.12-slim`       | Python Software Foundation License; Debian packages under their individual licenses        | Forms the YiQiao API and MCP companion image bases. Preserve `/usr/share/doc` copyright data and upstream image notices when redistributing.                                                                                       |
| `node:20-alpine`         | Node.js MIT; Alpine packages under their individual licenses                               | Forms the YiQiao dashboard image base. Preserve package copyright and license metadata when redistributing.                                                                                                                       |
| `pgvector/pgvector:pg17` | PostgreSQL License for PostgreSQL and pgvector                                             | Pulled as a separate service. Preserve the PostgreSQL and pgvector notices if mirrored or repackaged.                                                                                                                             |
| `neo4j:5-community`      | GNU General Public License v3 for Neo4j Community Edition, plus bundled component licenses | Pulled as a separate service and is not relicensed by YiQiao. A distributor that mirrors or repackages it must satisfy GPLv3 corresponding-source and notice obligations. Enterprise-only features and licenses are not included. |

Base images also contain operating-system packages not enumerated here. Before
publishing a release image, generate an SBOM and retain the license material
reported by the image package database. Pinning or mirroring an image does not
change its license.

## Obtaining License Texts

The Apache-2.0 text for YiQiao and its derived source is in [LICENSE](LICENSE).
Dependency distributions include their own license files in Python wheel
metadata, npm package directories, or operating-system package metadata. Source
and license texts are also available from the project links above. Release
artifacts should retain those files and include this notice.
