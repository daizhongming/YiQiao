// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "./middleware";

describe("Dashboard middleware connector paths", () => {
  it.each([
    "/.well-known/oauth-authorization-server",
    "/.well-known/service-capabilities",
    "/api/backend/auth/login",
    "/oauth/device_authorization",
    "/oauth/health",
    "/oauth/revoke",
    "/oauth/token",
    "/memories",
    "/search",
    "/v1/ping",
    "/v1/ping/",
  ])("allows the public protocol route %s without a session", async (path) => {
    const response = await middleware(
      new NextRequest(`http://127.0.0.1:3101${path}`),
    );

    expect(response.headers.get("x-middleware-next")).toBe("1");
  });

  it("keeps OAuth management routes behind Dashboard authentication", async () => {
    const response = await middleware(
      new NextRequest("http://127.0.0.1:3101/oauth/applications"),
    );

    expect(response.status).toBe(307);
    const location = new URL(response.headers.get("location") ?? "");
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe("/oauth/applications");
  });
});
