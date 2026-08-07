// This file was modified in 2026 by YiQiao contributors. See NOTICE.

export type McpClient = "codex" | "claude" | "openclaw" | "hermes";

export const DEFAULT_MCP_ENDPOINT = "http://127.0.0.1:8765/mcp";

export function normalizeMcpEndpoint(endpoint: string) {
  return endpoint.trim() || DEFAULT_MCP_ENDPOINT;
}

export function isValidMcpEndpoint(endpoint: string) {
  try {
    const url = new URL(normalizeMcpEndpoint(endpoint));
    return (
      (url.protocol === "http:" || url.protocol === "https:") &&
      Boolean(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash
    );
  } catch {
    return false;
  }
}

export function buildMcpClientConfig(client: McpClient, endpoint: string) {
  const url = normalizeMcpEndpoint(endpoint);
  if (!isValidMcpEndpoint(url)) {
    throw new TypeError("MCP endpoint must be a credential-free HTTP(S) URL.");
  }

  if (client === "codex") {
    return `[mcp_servers.yiqiao]
url = ${JSON.stringify(url)}
env_http_headers = { "X-API-Key" = "YIQIAO_API_KEY" }`;
  }

  if (client === "claude") {
    return JSON.stringify(
      {
        mcpServers: {
          yiqiao: {
            type: "http",
            url,
            headers: { "X-API-Key": "${YIQIAO_API_KEY}" },
          },
        },
      },
      null,
      2,
    );
  }

  if (client === "openclaw") {
    return JSON.stringify(
      {
        mcp: {
          servers: {
            yiqiao: {
              url,
              transport: "streamable-http",
              headers: { "X-API-Key": "${YIQIAO_API_KEY}" },
              connectTimeout: 5,
              timeout: 30,
            },
          },
        },
      },
      null,
      2,
    );
  }

  return `mcp_servers:
  yiqiao:
    url: ${JSON.stringify(url)}
    headers:
      X-API-Key: "\${YIQIAO_API_KEY}"
    timeout: 30`;
}
