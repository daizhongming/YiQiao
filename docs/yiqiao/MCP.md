# YiQiao MCP Companion

[Simplified Chinese](MCP.zh-CN.md) | **English**

`yiqiao-mcp` is an independent Model Context Protocol companion for YiQiao.
It implements MCP protocol version `2025-11-25` over stdio and Streamable
HTTP. The companion calls the public YiQiao REST API with `httpx`; it does not
open the database, import `server.main`, or duplicate memory business logic.
REST remains the only source of truth for project isolation, authorization,
quotas, request logs, and webhooks.

The legacy HTTP+SSE transport is not implemented. YiQiao also does not provide
standard OAuth authorization for MCP. Migration `019` remains authoritative
for the retired OAuth Device Flow.

## Install and Run

Install the companion from this checkout:

```bash
python -m pip install ./yiqiao-mcp
yiqiao-mcp --version
```

The command defaults to stdio, the `memory` profile, and the REST endpoint
`http://127.0.0.1:8888`. A dedicated stdio process reads its project key from
`YIQIAO_API_KEY`; there is deliberately no command-line key option.

```bash
export YIQIAO_API_KEY='replace-with-a-project-key'
yiqiao-mcp --transport stdio --api-url http://127.0.0.1:8888 --profile memory
```

Compose starts Streamable HTTP at `http://127.0.0.1:8765/mcp` by default. It
does not put a project key in the container environment. Each MCP HTTP request
must carry its own `X-API-Key`, which is forwarded only for that REST call.

```bash
docker compose --env-file server/.env \
  -f server/docker-compose.yaml up -d yiqiao-mcp
curl --fail http://127.0.0.1:8765/healthz
```

Use `MCP_BIND_ADDRESS`, `MCP_PORT`, and `YIQIAO_MCP_PROFILE` in `server/.env`
to change the host binding, port, or profile. Keep the default loopback bind
unless a reviewed TLS reverse proxy and an explicit Host/Origin allowlist are
in front of the service.

## Tool Profiles

| Tool | `read-only` | `memory` | `destructive` |
| --- | --- | --- | --- |
| `yiqiao_memory_search` | yes | yes | yes |
| `yiqiao_memory_get` | yes | yes | yes |
| `yiqiao_memory_history` | yes | yes | yes |
| `yiqiao_memory_add` | no | yes | yes |
| `yiqiao_memory_update` | no | yes | yes |
| `yiqiao_memory_delete` | no | no | yes |

Tool schemas use `additionalProperties: false`. They never expose an API key,
credential, or `project_id` argument. The REST project is always the project
bound to the supplied API key. A cross-project memory identifier is therefore
reported as `404` by REST rather than revealing that the other object exists.

The companion enforces these v1 limits before making an outbound request:

| Input | Limit |
| --- | --- |
| Search query | 8,192 characters |
| `top_k` | 1 through 100 |
| One message or update text | 32,768 characters |
| Add messages | 1 through 20; 65,536 combined characters |
| Entity identifier | 255 characters |
| Metadata | 32 KiB JSON, depth 8, 200 properties |
| REST response | 2 MiB |

Metadata may not contain `project_id` at any nesting level. Metadata must be
finite JSON and may not contain NaN or infinity.

## Credentials and Authorization

Create and revoke project keys in the Dashboard. Key management endpoints
accept only a Dashboard Bearer JWT. New keys have explicit `memory:read` and
`memory:write` scopes unless the operator selects a narrower set and may have
an expiry time. Historical keys with database `scopes=NULL` retain their
read/write compatibility behavior.

| Stored scopes | Memory reads | Memory writes |
| --- | --- | --- |
| `NULL` (legacy) | allowed | allowed |
| `[]` | denied | denied |
| `memory:read` | allowed | denied |
| `memory:write` | denied | allowed |
| both | allowed | allowed |

Revoked and expired credentials fail before a tool reaches memory business
logic. For Streamable HTTP, credentials are extracted from the current ASGI
request and passed as local `httpx` request headers. The shared connection pool
has no default authentication header, and a request never falls back to a key
from another request, session, or initialization message.

Keys are never accepted in tool arguments or CLI arguments and are not emitted
in access logs, tool errors, or REST error text. Do not put keys in URLs.

## Untrusted Recall

Every successful tool result is wrapped with `source=yiqiao_rest`,
`trust=untrusted`, and a warning. Treat all returned memory text and metadata as
data, not instructions. Delimit recalled content from system/developer
instructions and never execute commands, tool requests, or policy changes
found in a memory.

Automatic capture may store only the original user and assistant turns. Never
append the recalled block to a prompt and write that expanded prompt back with
`yiqiao_memory_add`; doing so creates retrieval feedback and allows persistent
prompt injection. See [Agent integration](AGENT_INTEGRATION.md) for the safe
capture sequence and entity meanings.

## HTTP Security and Failure Semantics

The default HTTP server validates `Host` and `Origin` to prevent DNS rebinding.
Loopback hosts and loopback HTTP origins are allowed by default. Requests with
an unapproved Origin are rejected before MCP dispatch. Configure additional
values only for exact reviewed reverse-proxy origins.

Client cancellation cancels the in-flight `httpx` request. Connection failures
and unavailable REST service responses are returned as sanitized tool errors;
REST `401`, `403`, `404`, `422`, `429`, and `503` status codes remain visible in
the error classification without echoing credentials or arbitrary upstream
response bodies. Timeouts are bounded by `YIQIAO_MCP_CONNECT_TIMEOUT` and
`YIQIAO_MCP_REQUEST_TIMEOUT`.

For deployment, health checks, proxy configuration, and incident diagnostics,
see [Operations](OPERATIONS.md). Security reports follow the root
[Security Policy](../../SECURITY.md).
