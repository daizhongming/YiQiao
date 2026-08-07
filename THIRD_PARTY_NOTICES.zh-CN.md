英文原文：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

非官方参考译文，发生歧义时以英文原文为准

# 第三方声明

YiQiao 使用第三方开源软件。本清单涵盖截至 2026-08-05 审查的直接运行时依赖、Dashboard 的直接构建依赖，以及具有实质性再分发要求的容器镜像。对于特定构建，`pyproject.toml`、`server/requirements.txt`、`yiqiao-mcp/pyproject.toml`、`server/dashboard/package.json` 和 `server/dashboard/pnpm-lock.yaml` 中的版本约束及解析后的版本具有权威性。

YiQiao 的 Apache License 2.0 不取代任何依赖项自身的许可证。依赖项随附的著作权声明和许可证文件必须保留在二进制发行版中。本文件仅供参考，不构成法律意见。

## 衍生源代码

YiQiao 包含来自 Mem0 开源项目（<https://github.com/mem0ai/mem0>）的修改后源代码，依据 Apache-2.0 许可。请参阅 [NOTICE](NOTICE) 和 [LICENSE](LICENSE)。

## Python 运行时

| 依赖项                    | 许可证                     | 项目源地址                                                         |
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

可选的 Python 提供方和向量存储扩展不会安装在默认 API 镜像中，除非在自定义构建中选择它们。在分发此类自定义镜像之前，必须审查这些扩展的许可证。

## MCP Companion 运行时

独立的 `yiqiao-mcp` 发行包声明了以下直接运行时依赖。为保证 companion 单独分发时的清单完整，此处也重复列出 API 已使用的依赖。

| 依赖项       | 许可证       | 项目源地址                                                 |
| ------------ | ------------ | ---------------------------------------------------------- |
| `anyio`      | MIT          | <https://github.com/agronholm/anyio>                       |
| `httpx`      | BSD-3-Clause | <https://github.com/encode/httpx>                          |
| `jsonschema` | MIT          | <https://github.com/python-jsonschema/jsonschema>          |
| `mcp`        | MIT          | <https://github.com/modelcontextprotocol/python-sdk>       |
| `pydantic`   | MIT          | <https://github.com/pydantic/pydantic>                     |
| `starlette`  | BSD-3-Clause | <https://github.com/Kludex/starlette>                      |
| `uvicorn`    | BSD-3-Clause | <https://github.com/encode/uvicorn>                        |

companion 使用 `hatchling`（MIT）构建发行包，可选测试依赖为 `pytest`（MIT）和 `pytest-asyncio`（Apache-2.0）。这些构建和测试软件包不会安装到运行时容器中。

## Dashboard 运行时

以下直接运行时软件包采用 MIT 许可证：

`@hookform/resolvers`、所有直接的 `@radix-ui/react-*` 软件包、
`@reduxjs/toolkit`、`@tanstack/react-table`、`axios`、`clsx`、`cmdk`、`date-fns`、
`framer-motion`、`lodash`、`next`、`next-themes`、`react`、
`react-copy-to-clipboard`、`react-day-picker`、`react-dom`、
`react-force-graph-2d`、`react-hook-form`、`react-redux`、
`react-syntax-highlighter`、`recharts`、`redux`、`sonner`、`tailwind-merge`、
`tailwindcss-animate`、`use-debounce`、`uuid`、`vaul` 和 `zod`。

其他直接运行时依赖的许可证如下：

| 依赖项                     | 许可证     | 项目源地址                               |
| -------------------------- | ---------- | ---------------------------------------- |
| `class-variance-authority` | Apache-2.0 | <https://github.com/joe-bell/cva>        |
| `lucide-react`             | ISC        | <https://github.com/lucide-icons/lucide> |

构建时和测试软件包 `@tailwindcss/typography`、`@testing-library/react`、直接的 `@types/*` 软件包、`autoprefixer`、`eslint`、`eslint-config-next`、`jsdom`、`postcss`、`prettier`、`tailwindcss` 和 `vitest` 采用 MIT 许可证。`typescript` 采用 Apache-2.0 许可证。

除本直接依赖清单外，Dashboard 锁定文件还包含传递依赖。对于解析后的构建，这些依赖的软件包元数据及其捆绑的许可证文件构成权威声明。已发布的 Dashboard 镜像将生产环境许可证清单、收集的软件包元数据和许可证文件存放在 `/usr/share/licenses/yiqiao/npm/` 下。

## Dashboard 字体

Dashboard 捆绑了从上游 Mem0 导入且未经修改的 Inter 4.001、Fustat 1.010、DM Mono 1.000 和 Roboto Mono 3.000 字体二进制文件。Inter、Fustat 和 DM Mono 依据 SIL Open Font License 1.1 许可；Roboto Mono 依据 Apache-2.0 许可。经审查的文件哈希、嵌入式著作权声明、版本、固定的历史许可证来源以及完整 OFL 文本，均载于[捆绑字体声明](server/dashboard/public/fonts/FONT_LICENSES.md)。除浏览器资源目录外，Dashboard 镜像还会将这些声明复制到 `/usr/share/licenses/yiqiao/fonts/` 下。

## 容器镜像

| 镜像系列                 | 实质性许可证                                                                               | 分发说明                                                                                                                                                                                                                          |
| ------------------------ | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python:3.12-slim`       | Python Software Foundation License；Debian 软件包适用其各自的许可证                        | 构成 YiQiao API 与 MCP companion 镜像的基础。再分发时必须保留 `/usr/share/doc` 著作权数据和上游镜像声明。                                                                                                                          |
| `node:20-alpine`         | Node.js MIT；Alpine 软件包适用其各自的许可证                                               | 构成 YiQiao Dashboard 镜像的基础。再分发时必须保留软件包著作权及许可证元数据。                                                                                                                                                      |
| `pgvector/pgvector:pg17` | PostgreSQL 和 pgvector 均适用 PostgreSQL License                                           | 作为独立服务拉取。如进行镜像或重新打包，必须保留 PostgreSQL 和 pgvector 的声明。                                                                                                                                                    |
| `neo4j:5-community`      | Neo4j Community Edition 适用 GNU General Public License v3，另含捆绑组件各自的许可证       | 作为独立服务拉取，YiQiao 不会对其重新许可。对其进行镜像或重新打包的分发者必须履行 GPLv3 的对应源代码提供义务和声明义务。不包含仅限 Enterprise 的功能和许可证。                                                                       |

基础镜像还包含此处未逐项列出的操作系统软件包。在发布发行版镜像之前，必须生成 SBOM，并保留镜像软件包数据库所报告的许可证材料。固定或镜像某个镜像不会改变其许可证。

## 获取许可证文本

YiQiao 及其衍生源代码适用的 Apache-2.0 文本见 [LICENSE](LICENSE)。依赖项的发行包在 Python wheel 元数据、npm 软件包目录或操作系统软件包元数据中包含各自的许可证文件。源代码和许可证文本也可从上方项目链接获取。发行制品应保留这些文件并包含本声明。
