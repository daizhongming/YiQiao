// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { createHmac } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";

import { proxyPublicConnectorRequest } from "./public-connector-proxy";

describe("public connector issuer proxy", () => {
  const signingSecret = "proxy-test-secret-that-is-at-least-32-bytes";
  const transportPeerActiveMarker = Symbol.for(
    "yiqiao.transportPeerPreloadActive",
  );

  const enableTransportPeerPreload = () => {
    Reflect.set(globalThis, transportPeerActiveMarker, true);
  };

  afterEach(() => {
    Reflect.deleteProperty(globalThis, transportPeerActiveMarker);
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("preserves the query and forwards only allowlisted and signed headers", async () => {
    enableTransportPeerPreload();
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:8101");
    vi.stubEnv("OAUTH_PROXY_HMAC_SECRET", signingSecret);
    vi.spyOn(Date, "now").mockReturnValue(1_725_000_123_000);
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
      new Request(
        "http://127.0.0.1:3101/oauth/token?scope=memory%3Aread&project_id=project-public&project_id=project-other",
        {
          method: "POST",
          headers: {
            Authorization: "Bearer yqoa_example",
            Cookie: "session=private",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Forwarded-For": "203.0.113.9",
            "X-Project-ID": "project-public",
            "X-Untrusted-Header": "hidden",
            "X-YiQiao-Proxy-Client-IP": "198.51.100.19",
            "X-YiQiao-Proxy-Signature": "spoofed",
            "X-YiQiao-Proxy-Timestamp": "1",
            "X-YiQiao-Transport-Peer": "2001:0db8:0:0:0:0:0:1",
          },
          body: "grant_type=refresh_token",
        },
      ),
      "/oauth/token",
      "POST",
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.has("set-cookie")).toBe(false);
    const [target, init] = upstreamFetch.mock.calls[0];
    expect(String(target)).toBe(
      "http://127.0.0.1:8101/oauth/token?scope=memory%3Aread&project_id=project-public&project_id=project-other",
    );
    const headers = init.headers as Headers;
    expect(headers.get("authorization")).toBe("Bearer yqoa_example");
    expect(headers.get("x-project-id")).toBe("project-public");
    expect(headers.has("cookie")).toBe(false);
    expect(headers.has("x-forwarded-for")).toBe(false);
    expect(headers.has("x-untrusted-header")).toBe(false);
    expect(headers.has("x-yiqiao-transport-peer")).toBe(false);

    const timestamp = "1725000123";
    const peer = "2001:db8::1";
    const expectedSignature = createHmac("sha256", signingSecret)
      .update(
        `v1\n${timestamp}\nPOST\n/oauth/token?scope=memory%3Aread&project_id=project-public&project_id=project-other\n${peer}`,
        "utf8",
      )
      .digest("hex");
    expect(headers.get("x-yiqiao-proxy-client-ip")).toBe(peer);
    expect(headers.get("x-yiqiao-proxy-timestamp")).toBe(timestamp);
    expect(headers.get("x-yiqiao-proxy-signature")).toBe(expectedSignature);
    expect(expectedSignature).toMatch(/^[0-9a-f]{64}$/);
  });

  it("returns 503 when the signing secret or transport peer is unavailable", async () => {
    enableTransportPeerPreload();
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:8101");
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);

    const request = (peer?: string) =>
      new Request("http://127.0.0.1:3101/oauth/health", {
        headers: peer ? { "X-YiQiao-Transport-Peer": peer } : undefined,
      });

    vi.stubEnv("OAUTH_PROXY_HMAC_SECRET", "");
    expect(
      (
        await proxyPublicConnectorRequest(
          request("192.0.2.10"),
          "/oauth/health",
          "GET",
        )
      ).status,
    ).toBe(503);

    vi.stubEnv("OAUTH_PROXY_HMAC_SECRET", "x".repeat(31));
    expect(
      (
        await proxyPublicConnectorRequest(
          request("192.0.2.10"),
          "/oauth/health",
          "GET",
        )
      ).status,
    ).toBe(503);

    vi.stubEnv("OAUTH_PROXY_HMAC_SECRET", signingSecret);
    expect(
      (await proxyPublicConnectorRequest(request(), "/oauth/health", "GET"))
        .status,
    ).toBe(503);
    expect(
      (
        await proxyPublicConnectorRequest(
          request("not-an-ip"),
          "/oauth/health",
          "GET",
        )
      ).status,
    ).toBe(503);
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("returns 503 when a peer header arrives without the transport preload", async () => {
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:8101");
    vi.stubEnv("OAUTH_PROXY_HMAC_SECRET", signingSecret);
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);

    const response = await proxyPublicConnectorRequest(
      new Request("http://127.0.0.1:3101/oauth/health", {
        headers: { "X-YiQiao-Transport-Peer": "192.0.2.10" },
      }),
      "/oauth/health",
      "GET",
    );

    expect(response.status).toBe(503);
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("rejects an oversized body without relying on Content-Length", async () => {
    enableTransportPeerPreload();
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:8101");
    vi.stubEnv("OAUTH_PROXY_HMAC_SECRET", signingSecret);
    const upstreamFetch = vi.fn();
    vi.stubGlobal("fetch", upstreamFetch);
    let chunksProduced = 0;
    let cancelled = false;
    const fragmentedBody = new ReadableStream<Uint8Array>({
      pull(controller) {
        chunksProduced += 1;
        if (chunksProduced > 10_241) {
          controller.error(
            new Error("The proxy failed to stop the oversized stream."),
          );
          return;
        }
        controller.enqueue(new Uint8Array(1024));
      },
      cancel() {
        cancelled = true;
      },
    });
    const request = new Request("http://127.0.0.1:3101/oauth/token", {
      method: "POST",
      headers: { "X-YiQiao-Transport-Peer": "192.0.2.10" },
      body: fragmentedBody,
      duplex: "half",
    } as RequestInit & { duplex: "half" });
    expect(request.headers.has("content-length")).toBe(false);

    const response = await proxyPublicConnectorRequest(
      request,
      "/oauth/token",
      "POST",
    );

    expect(response.status).toBe(413);
    expect(chunksProduced).toBe(10_241);
    expect(cancelled).toBe(true);
    expect(upstreamFetch).not.toHaveBeenCalled();
  });

  it("fails closed for recursion, methods, redirects, and missing configuration", async () => {
    enableTransportPeerPreload();
    const request = (url: string, method = "GET") =>
      new Request(url, {
        method,
        headers: { "X-YiQiao-Transport-Peer": "192.0.2.10" },
      });

    vi.stubEnv("OAUTH_PROXY_HMAC_SECRET", signingSecret);
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:3101");
    expect(
      (
        await proxyPublicConnectorRequest(
          request("http://127.0.0.1:3101/oauth/token"),
          "/oauth/token",
          "GET",
        )
      ).status,
    ).toBe(503);

    expect(
      (
        await proxyPublicConnectorRequest(
          request("http://127.0.0.1:3102/oauth/token"),
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
          request("http://127.0.0.1:3102/oauth/health"),
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
          request("http://127.0.0.1:3102/oauth/health"),
          "/oauth/health",
          "GET",
        )
      ).status,
    ).toBe(503);
  });
});
