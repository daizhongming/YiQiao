// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";
import { middleware } from "./middleware";

describe("Dashboard middleware public paths", () => {
  it.each(["/api/auth/refresh", "/api/backend/auth/login", "/api/health"])(
    "allows the public application route %s without a session",
    async (path) => {
      const response = await middleware(
        new NextRequest(`http://127.0.0.1:3101${path}`),
      );

      expect(response.headers.get("x-middleware-next")).toBe("1");
    },
  );

  it("does not expose retired OAuth protocol routes", async () => {
    const response = await middleware(
      new NextRequest("http://127.0.0.1:3101/oauth/device_authorization"),
    );

    expect(response.status).toBe(307);
    const location = new URL(response.headers.get("location") ?? "");
    expect(location.pathname).toBe("/login");
    expect(location.searchParams.get("next")).toBe(
      "/oauth/device_authorization",
    );
  });
});
