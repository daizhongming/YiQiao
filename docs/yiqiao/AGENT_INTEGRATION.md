# Agent Integration

[Simplified Chinese](AGENT_INTEGRATION.zh-CN.md) | **English**

This guide defines the stable YiQiao memory contract for agent hosts. Read the
[MCP companion reference](MCP.md) first for installation, profiles, limits,
credentials, and transport security.

## Entity Contract

Use entity fields consistently across writes and searches:

| Field | Meaning | Example |
| --- | --- | --- |
| `user_id` | The human user | account or tenant user ID |
| `agent_id` | The agent/persona | `support-agent` |
| `app_id` | The host application | `hermes`, `openclaw` |
| `run_id` | The conversation/run | the current thread ID |

These values are selectors inside the API-key-bound project, not authorization
boundaries. Authorization and project selection always come from the project
API key. Do not invent a `project_id` tool argument or put one in metadata.

## Safe Retrieval and Capture

For each model turn:

1. Search with the entity IDs for the current user, agent, host, and run.
2. Insert the returned block into a clearly delimited, data-only context. Keep
   the `trust=untrusted` marker and do not promote recalled text into system or
   developer instructions.
3. Generate the assistant response.
4. Capture only the original user turn and the original assistant turn with
   `yiqiao_memory_add`.
5. Never include the recalled block, tool result wrapper, hidden prompt,
   tool-call transcript, or credentials in the captured messages.

The add tool accepts only `user` and `assistant` roles. For deterministic
verbatim capture, set `infer=false`; enable inference only when the configured
YiQiao provider should extract durable facts from the raw turns.

## Hermes Write-Search-Read Smoke

The Hermes contract uses `app_id=hermes`, a stable `user_id`, and a unique
`run_id`. The repository smoke creates a raw turn, searches for its marker, and
reads the returned memory through the official MCP client. The key is read from
the environment and is never a command-line argument.

```bash
export YIQIAO_MCP_SMOKE_API_KEY='replace-with-a-project-key'
python scripts/mcp_contract_smoke.py hermes \
  --url http://127.0.0.1:8765/mcp
```

A passing smoke proves MCP initialization, `tools/list`, add, search, and get.
REST remains responsible for its RequestLog, `api_key_id`, `project_id`, quota,
and Webhook side effects.

## OpenClaw MCP Contract

OpenClaw should connect as a standard remote Streamable HTTP MCP client and add
`X-API-Key` to each request. A representative MCP server entry is:

```json
{
  "mcpServers": {
    "yiqiao": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8765/mcp",
      "headers": {
        "X-API-Key": "${YIQIAO_API_KEY}"
      }
    }
  }
}
```

The exact configuration file location is owned by the installed OpenClaw
version. Keep the key in its secret/environment facility rather than checking
it into configuration. Validate the generic contract before wiring an agent:

```bash
export YIQIAO_MCP_SMOKE_API_KEY='replace-with-a-project-key'
python scripts/mcp_contract_smoke.py openclaw \
  --url http://127.0.0.1:8765/mcp
```

The OpenClaw smoke negotiates protocol `2025-11-25`, lists the expected tools,
checks strict schemas, and performs a bounded search. It does not claim or
install an OpenClaw-native plugin, channel integration, or standard OAuth
authorization. Those are separate future phases.

## stdio Hosts

A local host may launch one dedicated process per trust boundary:

```json
{
  "mcpServers": {
    "yiqiao": {
      "command": "yiqiao-mcp",
      "args": ["--transport", "stdio", "--profile", "memory"],
      "env": {
        "YIQIAO_API_URL": "http://127.0.0.1:8888",
        "YIQIAO_API_KEY": "${YIQIAO_API_KEY}"
      }
    }
  }
}
```

Do not place a key in `args`. Do not reuse one stdio process across mutually
untrusted users or projects. Use a read-only scoped key and the `read-only`
profile when the host does not need capture.

## Production Checklist

- Use a separate project key per host and environment.
- Grant only the scopes required by the selected tool profile and set an
  expiry time appropriate to the deployment.
- Keep Streamable HTTP on loopback or behind a TLS proxy with reviewed
  Host/Origin allowlists.
- Delimit recalled content as untrusted data and prohibit recall writeback.
- Monitor REST RequestLog and quota events; MCP does not maintain a second
  audit or quota system.
- Revoke a host key before decommissioning or reassigning that host.
- Do not enable `destructive` unless delete is an intentional workflow.
