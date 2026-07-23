// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { NextRequest, NextResponse } from "next/server";
import { AUTH_ENDPOINTS } from "@/utils/api-endpoints";
import { getServerApiUrl } from "@/lib/server-api-url";

const PUBLIC_PATHS = [
  "/_next",
  "/api/auth",
  "/api/backend",
  "/api/health",
  "/fonts",
  "/favicon",
];

// Connector clients bootstrap and exchange credentials without a Dashboard
// session. Keep management endpoints under `/oauth/*` authenticated by using
// an exact allowlist for the public protocol surface.
const PUBLIC_CONNECTOR_PATHS = new Set([
  "/.well-known/oauth-authorization-server",
  "/.well-known/service-capabilities",
  "/oauth/device_authorization",
  "/oauth/health",
  "/oauth/revoke",
  "/oauth/token",
  "/memories",
  "/search",
  "/v1/ping",
  "/v1/ping/",
]);

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  if (
    PUBLIC_PATHS.some((p) => pathname.startsWith(p)) ||
    PUBLIC_CONNECTOR_PATHS.has(pathname)
  ) {
    return NextResponse.next();
  }

  const hasRefreshToken = request.cookies.has("yiqiao_refresh_token");

  if (pathname === "/" || pathname === "/login" || pathname === "/setup") {
    try {
      const res = await fetch(
        `${getServerApiUrl()}${AUTH_ENDPOINTS.SETUP_STATUS}`,
      );
      if (res.ok) {
        const { needsSetup } = await res.json();

        if (needsSetup && pathname !== "/setup") {
          return NextResponse.redirect(new URL("/setup", request.url));
        }
        if (!needsSetup && pathname === "/setup") {
          return NextResponse.redirect(new URL("/login", request.url));
        }
      }
    } catch {
      // API unreachable — fall through to default behavior
    }
  }

  if (pathname === "/login" || pathname === "/setup") {
    return NextResponse.next();
  }

  if (pathname === "/") {
    return NextResponse.redirect(
      new URL(hasRefreshToken ? "/dashboard" : "/login", request.url),
    );
  }

  if (!hasRefreshToken) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|fonts|images|icons).*)"],
};
