// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { describe, expect, it } from "vitest";

import {
  DEFAULT_MCP_ENDPOINT,
  buildMcpClientConfig,
  isValidMcpEndpoint,
  normalizeMcpEndpoint,
  type McpClient,
} from "@/app/(root)/dashboard/install/mcp-config";
import { translateText } from "./i18n";

describe("YiQiao integration translations", () => {
  it("provides Chinese labels for the SDK and MCP quick starts", () => {
    expect(translateText("Integrations", "zh")).toBe("集成");
    expect(translateText("Step 1: Install the SDK", "zh")).toBe(
      "第 1 步：安装 SDK",
    );
    expect(translateText("Step 2: Add and search memories", "zh")).toBe(
      "第 2 步：添加并检索记忆",
    );
    expect(translateText("API Reference", "zh")).toBe("接口文档");
    expect(translateText("MCP Agents", "zh")).toBe("MCP 智能体");
    expect(translateText("Step 1: Start the MCP service", "zh")).toBe(
      "第 1 步：启动 MCP 服务",
    );
    expect(translateText("Open API Keys", "zh")).toBe("打开 API 密钥");
    expect(translateText("Merge into configuration", "zh")).toBe("合并到配置");
    expect(
      translateText(
        "This uses OpenClaw's standard MCP registry, not a native plugin or OAuth flow.",
        "zh",
      ),
    ).toBe("此方式使用 OpenClaw 标准 MCP 注册表，并非原生插件或 OAuth 流程。");
  });
});

describe("YiQiao MCP client configuration", () => {
  const clients: McpClient[] = ["codex", "claude", "openclaw", "hermes"];

  it.each(clients)("builds a secret-free %s configuration", (client) => {
    const config = buildMcpClientConfig(client, DEFAULT_MCP_ENDPOINT);

    expect(config).toContain(DEFAULT_MCP_ENDPOINT);
    expect(config).toContain("X-API-Key");
    expect(config).toContain("YIQIAO_API_KEY");
    expect(config).not.toContain("project_id");
    expect(config.toLowerCase()).not.toContain("oauth");
    expect(config).not.toMatch(/transport["']?\s*[:=]\s*["']sse["']/i);
  });

  it("uses environment interpolation only where the client supports it", () => {
    expect(buildMcpClientConfig("codex", DEFAULT_MCP_ENDPOINT)).toContain(
      '"YIQIAO_API_KEY"',
    );
    for (const client of ["claude", "openclaw", "hermes"] as const) {
      expect(buildMcpClientConfig(client, DEFAULT_MCP_ENDPOINT)).toContain(
        "${YIQIAO_API_KEY}",
      );
    }
  });

  it("uses each client's current MCP configuration shape", () => {
    expect(buildMcpClientConfig("codex", DEFAULT_MCP_ENDPOINT)).toContain(
      "env_http_headers",
    );
    expect(buildMcpClientConfig("claude", DEFAULT_MCP_ENDPOINT)).toContain(
      '"type": "http"',
    );
    expect(buildMcpClientConfig("openclaw", DEFAULT_MCP_ENDPOINT)).toContain(
      '"transport": "streamable-http"',
    );
    expect(buildMcpClientConfig("hermes", DEFAULT_MCP_ENDPOINT)).toContain(
      "mcp_servers:",
    );
  });

  it("normalizes an empty endpoint and validates credential-free HTTP(S) URLs", () => {
    expect(normalizeMcpEndpoint("   ")).toBe(DEFAULT_MCP_ENDPOINT);
    expect(isValidMcpEndpoint("https://memory.example.com/mcp")).toBe(true);
    expect(isValidMcpEndpoint("localhost:8765/mcp")).toBe(false);
    expect(isValidMcpEndpoint("file:///tmp/yiqiao.sock")).toBe(false);
    expect(isValidMcpEndpoint("https://user:pass@example.com/mcp")).toBe(false);
    expect(isValidMcpEndpoint("https://example.com/mcp?key=secret")).toBe(
      false,
    );
    expect(isValidMcpEndpoint("https://example.com/mcp#fragment")).toBe(false);
    expect(() => buildMcpClientConfig("codex", "javascript:alert(1)")).toThrow(
      "credential-free HTTP(S) URL",
    );
  });
});
