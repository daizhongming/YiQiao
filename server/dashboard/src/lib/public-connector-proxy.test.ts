// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { afterEach, describe, expect, it, vi } from "vitest";

import { proxyPublicConnectorRequest } from "./public-connector-proxy";

describe("public connector issuer proxy", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("forwards only allowlisted request and response headers", async () => {
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:8101");
    const upstreamFetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ protocol_version: "1.0" }), {
        headers: {
          "Content-Type": "application/json",
          "Set-Cookie": "secret=value",
          "X-Upstream-Secret": "hidden",
        },
      }),
    );
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await proxyPublicConnectorRequest(
      new Request("http://127.0.0.1:3101/oauth/token", {
        method: "POST",
        headers: {
          Authorization: "Bearer yqoa_example",
          Cookie: "session=private",
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Forwarded-For": "203.0.113.9",
        },
        body: "grant_type=refresh_token",
      }),
      "/oauth/token",
      "POST",
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.has("set-cookie")).toBe(false);
    const [target, init] = upstreamFetch.mock.calls[0];
    expect(String(target)).toBe("http://127.0.0.1:8101/oauth/token");
    const headers = init.headers as Headers;
    expect(headers.get("authorization")).toBe("Bearer yqoa_example");
    expect(headers.has("cookie")).toBe(false);
    expect(headers.has("x-forwarded-for")).toBe(false);
  });

  it("fails closed for recursion, methods, redirects, and missing configuration", async () => {
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:3101");
    expect(
      (
        await proxyPublicConnectorRequest(
          new Request("http://127.0.0.1:3101/oauth/token"),
          "/oauth/token",
          "GET",
        )
      ).status,
    ).toBe(503);

    expect(
      (
        await proxyPublicConnectorRequest(
          new Request("http://127.0.0.1:3102/oauth/token"),
          "/oauth/token",
          "POST",
        )
      ).status,
    ).toBe(405);

    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:8101");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 307 })),
    );
    expect(
      (
        await proxyPublicConnectorRequest(
          new Request("http://127.0.0.1:3102/oauth/health"),
          "/oauth/health",
          "GET",
        )
      ).status,
    ).toBe(502);

    vi.stubEnv("API_INTERNAL_URL", "");
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");
    expect(
      (
        await proxyPublicConnectorRequest(
          new Request("http://127.0.0.1:3102/oauth/health"),
          "/oauth/health",
          "GET",
        )
      ).status,
    ).toBe(503);
  });
});
