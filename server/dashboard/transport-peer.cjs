// This file was modified in 2026 by YiQiao contributors. See NOTICE.

const http = require("node:http");
const net = require("node:net");

const FORWARDED_FOR_HEADER = "x-forwarded-for";
const TRANSPORT_PEER_HEADER = "x-yiqiao-transport-peer";
const ACTIVE_MARKER = Symbol.for("yiqiao.transportPeerPreloadActive");
const PATCH_MARKER = Symbol.for("yiqiao.transportPeerPatch");
const SCOPED_IPV6_RE = /^[A-Za-z0-9_.-]{1,64}$/;

function normalizeIp(value) {
  if (typeof value !== "string") return null;
  const candidate = value.trim();
  if (!candidate) return null;

  let address = candidate;
  let scope = "";
  const scopeSeparator = candidate.indexOf("%");
  if (scopeSeparator !== -1) {
    if (candidate.indexOf("%", scopeSeparator + 1) !== -1) return null;
    address = candidate.slice(0, scopeSeparator);
    scope = candidate.slice(scopeSeparator + 1);
    if (!SCOPED_IPV6_RE.test(scope) || net.isIP(address) !== 6) return null;
  }

  const version = net.isIP(address);
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

function forwardedForValues(request) {
  const rawHeaders = request && request.rawHeaders;
  if (Array.isArray(rawHeaders)) {
    const values = [];
    for (let index = 0; index + 1 < rawHeaders.length; index += 2) {
      if (String(rawHeaders[index]).toLowerCase() === FORWARDED_FOR_HEADER) {
        values.push(String(rawHeaders[index + 1]));
      }
    }
    if (values.length) return values;
  }

  const headers = request && request.headers;
  if (!headers || typeof headers !== "object") return [];
  const value = headers[FORWARDED_FOR_HEADER];
  if (Array.isArray(value)) return value.map(String);
  return typeof value === "string" ? [value] : [];
}

function gatewayRateLimitConfirmed() {
  return process.env.OAUTH_GATEWAY_RATE_LIMIT_CONFIRMED === "true";
}

function selectTransportPeer(
  request,
  trustForwardedFor = gatewayRateLimitConfirmed(),
) {
  if (!request || typeof request !== "object") return null;
  if (!trustForwardedFor) {
    return normalizeIp(request.socket && request.socket.remoteAddress);
  }

  const forwardedFor = forwardedForValues(request);
  if (forwardedFor.length !== 1) return null;
  return normalizeIp(forwardedFor[0]);
}

function rewriteRawHeaders(request, peer) {
  if (!Array.isArray(request.rawHeaders)) return;

  const retained = [];
  for (let index = 0; index + 1 < request.rawHeaders.length; index += 2) {
    const name = String(request.rawHeaders[index]);
    const normalizedName = name.toLowerCase();
    if (
      normalizedName !== FORWARDED_FOR_HEADER &&
      normalizedName !== TRANSPORT_PEER_HEADER
    ) {
      retained.push(name, request.rawHeaders[index + 1]);
    }
  }
  if (peer) {
    retained.push("X-YiQiao-Transport-Peer", peer);
    retained.push("X-Forwarded-For", peer);
  }
  request.rawHeaders.splice(0, request.rawHeaders.length, ...retained);
}

function rewriteParsedHeaders(request, peer) {
  const headers = request.headers;
  if (!headers || typeof headers !== "object") return;

  for (const name of Object.keys(headers)) {
    const normalizedName = name.toLowerCase();
    if (
      normalizedName === FORWARDED_FOR_HEADER ||
      normalizedName === TRANSPORT_PEER_HEADER
    ) {
      delete headers[name];
    }
  }
  if (peer) {
    headers[TRANSPORT_PEER_HEADER] = peer;
    headers[FORWARDED_FOR_HEADER] = peer;
  }
}

function applyTransportPeer(request) {
  const peer = selectTransportPeer(request);
  rewriteRawHeaders(request, peer);
  rewriteParsedHeaders(request, peer);
  return peer;
}

if (!http.Server.prototype[PATCH_MARKER]) {
  const originalEmit = http.Server.prototype.emit;
  Object.defineProperty(http.Server.prototype, PATCH_MARKER, {
    value: true,
  });
  Object.defineProperty(http.Server.prototype, "emit", {
    configurable: true,
    writable: true,
    value: function emit(event, ...args) {
      if (event === "request") applyTransportPeer(args[0]);
      return originalEmit.call(this, event, ...args);
    },
  });
}
globalThis[ACTIVE_MARKER] = true;

module.exports = {
  applyTransportPeer,
  normalizeIp,
  selectTransportPeer,
};
