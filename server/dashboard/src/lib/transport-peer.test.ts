// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { createRequire } from "node:module";
import { afterEach, describe, expect, it, vi } from "vitest";

type FakeRequest = {
  headers: Record<string, string | string[] | undefined>;
  rawHeaders: string[];
  socket: { remoteAddress?: string };
};

type TransportPeerModule = {
  applyTransportPeer(request: FakeRequest): string | null;
  normalizeIp(value: unknown): string | null;
  selectTransportPeer(
    request: FakeRequest,
    trustForwardedFor?: boolean,
  ): string | null;
};

const loadModule = createRequire(import.meta.url);
const { applyTransportPeer, normalizeIp, selectTransportPeer } = loadModule(
  "../../transport-peer.cjs",
) as TransportPeerModule;

describe("transport peer preload", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("uses and canonicalizes the socket peer while discarding spoofed headers", () => {
    const request: FakeRequest = {
      headers: {
        host: "connector.example",
        "x-forwarded-for": "203.0.113.20",
        "x-yiqiao-transport-peer": "198.51.100.20",
      },
      rawHeaders: [
        "Host",
        "connector.example",
        "X-Forwarded-For",
        "203.0.113.20",
        "X-YiQiao-Transport-Peer",
        "198.51.100.20",
      ],
      socket: { remoteAddress: "::ffff:192.0.2.10" },
    };

    expect(applyTransportPeer(request)).toBe("::ffff:c000:20a");
    expect(request.headers["x-forwarded-for"]).toBe("::ffff:c000:20a");
    expect(request.headers["x-yiqiao-transport-peer"]).toBe("::ffff:c000:20a");
    expect(request.rawHeaders).toEqual([
      "Host",
      "connector.example",
      "X-YiQiao-Transport-Peer",
      "::ffff:c000:20a",
      "X-Forwarded-For",
      "::ffff:c000:20a",
    ]);
  });

  it("uses one gateway-sanitized X-Forwarded-For value when explicitly confirmed", () => {
    vi.stubEnv("OAUTH_GATEWAY_RATE_LIMIT_CONFIRMED", "true");
    const request: FakeRequest = {
      headers: { "x-forwarded-for": "2001:0db8:0:0:0:0:0:5" },
      rawHeaders: ["X-Forwarded-For", "2001:0db8:0:0:0:0:0:5"],
      socket: { remoteAddress: "10.0.0.4" },
    };

    expect(applyTransportPeer(request)).toBe("2001:db8::5");
    expect(request.headers["x-forwarded-for"]).toBe("2001:db8::5");
    expect(request.headers["x-yiqiao-transport-peer"]).toBe("2001:db8::5");
  });

  it("fails closed for missing, duplicate, chained, or invalid gateway values", () => {
    const request = (rawHeaders: string[], value?: string): FakeRequest => ({
      headers: value === undefined ? {} : { "x-forwarded-for": value },
      rawHeaders,
      socket: { remoteAddress: "10.0.0.4" },
    });

    expect(selectTransportPeer(request([]), true)).toBeNull();
    expect(
      selectTransportPeer(
        request(
          ["X-Forwarded-For", "192.0.2.1, 198.51.100.1"],
          "192.0.2.1, 198.51.100.1",
        ),
        true,
      ),
    ).toBeNull();
    const duplicate = request(
      ["X-Forwarded-For", "192.0.2.1", "X-Forwarded-For", "198.51.100.1"],
      "192.0.2.1, 198.51.100.1",
    );
    vi.stubEnv("OAUTH_GATEWAY_RATE_LIMIT_CONFIRMED", "true");
    expect(applyTransportPeer(duplicate)).toBeNull();
    expect(duplicate.headers["x-forwarded-for"]).toBeUndefined();
    expect(duplicate.headers["x-yiqiao-transport-peer"]).toBeUndefined();
    expect(duplicate.rawHeaders).toEqual([]);
    expect(
      selectTransportPeer(
        request(["X-Forwarded-For", "not-an-ip"], "not-an-ip"),
        true,
      ),
    ).toBeNull();
    expect(normalizeIp("192.0.2.1")).toBe("192.0.2.1");
    expect(normalizeIp("2001:0db8:0:0:0:0:0:1")).toBe("2001:db8::1");
    expect(normalizeIp("127.000.000.001")).toBeNull();
  });
});
