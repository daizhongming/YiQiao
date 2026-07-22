// This file was modified in 2026 by YiQiao contributors. See NOTICE.

const MAX_PROXY_BODY_BYTES = 10 * 1024 * 1024;
const PROXY_TIMEOUT_MS = 30_000;
const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "authorization",
  "content-type",
  "user-agent",
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

function requestHeaders(request: Request): Headers {
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const requestId = request.headers.get("x-request-id");
  if (requestId && /^[A-Za-z0-9._:-]{1,64}$/.test(requestId)) {
    headers.set("x-request-id", requestId);
  }
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

  const upstream = new URL(upstreamPath.replace(/^\//, ""), baseUrl);
  if (upstream.origin === new URL(request.url).origin) {
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
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_PROXY_BODY_BYTES) {
      return connectorError(
        413,
        "invalid_request",
        "The request body is too large.",
      );
    }
  }

  let response: Response;
  try {
    response = await fetch(upstream, {
      method: expectedMethod,
      headers: requestHeaders(request),
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
