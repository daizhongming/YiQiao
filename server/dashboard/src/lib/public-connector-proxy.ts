// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { createHmac } from "node:crypto";
import { isIP } from "node:net";

const MAX_PROXY_BODY_BYTES = 10 * 1024 * 1024;
const PROXY_TIMEOUT_MS = 30_000;
const PROXY_CLIENT_IP_HEADER = "x-yiqiao-proxy-client-ip";
const PROXY_SIGNATURE_HEADER = "x-yiqiao-proxy-signature";
const PROXY_TIMESTAMP_HEADER = "x-yiqiao-proxy-timestamp";
const TRANSPORT_PEER_HEADER = "x-yiqiao-transport-peer";
const TRANSPORT_PEER_ACTIVE_MARKER = Symbol.for(
  "yiqiao.transportPeerPreloadActive",
);
const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "authorization",
  "content-type",
  "user-agent",
  "x-project-id",
] as const;
const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "retry-after",
  "www-authenticate",
] as const;

function internalApiBaseUrl(): URL {
  const configured = (
    process.env.API_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    ""
  ).trim();
  if (!configured) {
    throw new Error("The internal YiQiao API URL is not configured.");
  }

  const parsed = new URL(configured);
  if (!new Set(["http:", "https:"]).has(parsed.protocol)) {
    throw new Error("The internal YiQiao API URL must use HTTP or HTTPS.");
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(
      "The internal YiQiao API URL must not contain credentials, a query, or a fragment.",
    );
  }
  parsed.pathname = `${parsed.pathname.replace(/\/$/, "")}/`;
  return parsed;
}

function connectorError(status: number, code: string, description: string) {
  return Response.json(
    { error: code, error_description: description, protocol_version: "1.0" },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        Pragma: "no-cache",
      },
    },
  );
}

function normalizeIp(value: string | null): string | null {
  const candidate = value?.trim() ?? "";
  if (!candidate) return null;

  let address = candidate;
  let scope = "";
  const scopeSeparator = candidate.indexOf("%");
  if (scopeSeparator !== -1) {
    if (candidate.indexOf("%", scopeSeparator + 1) !== -1) return null;
    address = candidate.slice(0, scopeSeparator);
    scope = candidate.slice(scopeSeparator + 1);
    if (!/^[A-Za-z0-9_.-]{1,64}$/.test(scope) || isIP(address) !== 6) {
      return null;
    }
  }

  const version = isIP(address);
  if (version === 4) return address;
  if (version !== 6) return null;

  try {
    const hostname = new URL(`http://[${address}]/`).hostname;
    const normalized = hostname.slice(1, -1);
    return scope ? `${normalized}%${scope}` : normalized;
  } catch {
    return null;
  }
}

function proxySigningSecret(): string | null {
  const value = (process.env.OAUTH_PROXY_HMAC_SECRET || "").trim();
  return Buffer.byteLength(value, "utf8") >= 32 ? value : null;
}

function transportPeerPreloadActive(): boolean {
  return Reflect.get(globalThis, TRANSPORT_PEER_ACTIVE_MARKER) === true;
}

async function readRequestBody(request: Request): Promise<ArrayBuffer | null> {
  if (!request.body) return new ArrayBuffer(0);

  const reader = request.body.getReader();
  let body = new Uint8Array(0);
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const nextTotal = totalBytes + value.byteLength;
      if (nextTotal > MAX_PROXY_BODY_BYTES) {
        await reader.cancel().catch(() => undefined);
        return null;
      }
      if (nextTotal > body.byteLength) {
        const nextCapacity = Math.min(
          MAX_PROXY_BODY_BYTES,
          Math.max(
            nextTotal,
            body.byteLength ? body.byteLength * 2 : 64 * 1024,
          ),
        );
        const expanded = new Uint8Array(nextCapacity);
        expanded.set(body.subarray(0, totalBytes));
        body = expanded;
      }
      body.set(value, totalBytes);
      totalBytes = nextTotal;
    }
  } finally {
    reader.releaseLock();
  }

  return body.buffer.slice(0, totalBytes);
}

function requestHeaders(
  request: Request,
  peer: string,
  timestamp: string,
  signature: string,
): Headers {
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const requestId = request.headers.get("x-request-id");
  if (requestId && /^[A-Za-z0-9._:-]{1,64}$/.test(requestId)) {
    headers.set("x-request-id", requestId);
  }
  headers.set(PROXY_CLIENT_IP_HEADER, peer);
  headers.set(PROXY_TIMESTAMP_HEADER, timestamp);
  headers.set(PROXY_SIGNATURE_HEADER, signature);
  return headers;
}

export async function proxyPublicConnectorRequest(
  request: Request,
  upstreamPath: string,
  expectedMethod: "GET" | "POST",
): Promise<Response> {
  if (request.method !== expectedMethod) {
    return connectorError(
      405,
      "invalid_request",
      "The request method is not allowed.",
    );
  }

  let baseUrl: URL;
  try {
    baseUrl = internalApiBaseUrl();
  } catch {
    return connectorError(
      503,
      "temporarily_unavailable",
      "The connector service is not configured.",
    );
  }

  const peer = normalizeIp(request.headers.get(TRANSPORT_PEER_HEADER));
  const signingSecret = proxySigningSecret();
  if (!transportPeerPreloadActive() || !peer || !signingSecret) {
    return connectorError(
      503,
      "temporarily_unavailable",
      "The connector service is not configured.",
    );
  }

  const requestUrl = new URL(request.url);
  const upstream = new URL(upstreamPath.replace(/^\//, ""), baseUrl);
  upstream.search = requestUrl.search;
  if (upstream.origin === requestUrl.origin) {
    return connectorError(
      503,
      "temporarily_unavailable",
      "The connector service route is unavailable.",
    );
  }

  let body: ArrayBuffer | undefined;
  if (expectedMethod === "POST") {
    const declaredLength = Number(request.headers.get("content-length") || "0");
    if (
      Number.isFinite(declaredLength) &&
      declaredLength > MAX_PROXY_BODY_BYTES
    ) {
      return connectorError(
        413,
        "invalid_request",
        "The request body is too large.",
      );
    }
    try {
      body = (await readRequestBody(request)) ?? undefined;
    } catch {
      return connectorError(
        400,
        "invalid_request",
        "The request body could not be read.",
      );
    }
    if (body === undefined) {
      return connectorError(
        413,
        "invalid_request",
        "The request body is too large.",
      );
    }
  }

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const signaturePayload = `v1\n${timestamp}\n${expectedMethod.toUpperCase()}\n${upstream.pathname}${upstream.search}\n${peer}`;
  const signature = createHmac("sha256", signingSecret)
    .update(signaturePayload, "utf8")
    .digest("hex");

  let response: Response;
  try {
    response = await fetch(upstream, {
      method: expectedMethod,
      headers: requestHeaders(request, peer, timestamp, signature),
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(PROXY_TIMEOUT_MS),
    });
  } catch {
    return connectorError(
      502,
      "temporarily_unavailable",
      "The connector service could not be reached.",
    );
  }

  if (response.status >= 300 && response.status < 400) {
    return connectorError(
      502,
      "server_error",
      "The connector service returned an unexpected redirect.",
    );
  }

  const headers = new Headers({
    "Cache-Control": "no-store",
    Pragma: "no-cache",
  });
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = response.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (!headers.has("content-type") && response.status !== 204) {
    headers.set("content-type", "application/json");
  }

  const responseBody = await response.arrayBuffer();
  return new Response(responseBody.byteLength ? responseBody : null, {
    status: response.status,
    headers,
  });
}
