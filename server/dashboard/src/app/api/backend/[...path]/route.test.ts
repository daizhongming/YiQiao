// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { PATCH, POST } from "./route";

describe("Dashboard same-origin backend proxy", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("forwards the login request and returns the upstream response", async () => {
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:8888");
    const upstreamFetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ access_token: "access", refresh_token: "refresh" }),
        {
          status: 200,
          headers: {
            "Cache-Control": "no-store",
            "Content-Type": "application/json",
            "Set-Cookie": "must-not-leak=1",
          },
        },
      ),
    );
    vi.stubGlobal("fetch", upstreamFetch);

    const request = new NextRequest(
      "http://127.0.0.1:3000/api/backend/auth/login?source=dashboard",
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: "Bearer stale",
          "Content-Type": "application/json",
          Cookie: "session=private",
          "X-Project-ID": "default-project",
        },
        body: JSON.stringify({ email: "admin@example.test", password: "x" }),
      },
    );

    const response = await POST(request, {
      params: Promise.resolve({ path: ["auth", "login"] }),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      access_token: "access",
      refresh_token: "refresh",
    });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.has("set-cookie")).toBe(false);

    const [target, init] = upstreamFetch.mock.calls[0];
    expect(String(target)).toBe(
      "http://127.0.0.1:8888/auth/login?source=dashboard",
    );
    expect(init.method).toBe("POST");
    const headers = init.headers as Headers;
    expect(headers.get("authorization")).toBe("Bearer stale");
    expect(headers.get("x-project-id")).toBe("default-project");
    expect(headers.has("cookie")).toBe(false);
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(
      JSON.stringify({ email: "admin@example.test", password: "x" }),
    );
  });

  it("returns a stable service-unavailable error when the backend is offline", async () => {
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:8888");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    const response = await POST(
      new NextRequest("http://127.0.0.1:3000/api/backend/auth/login", {
        method: "POST",
        body: "{}",
      }),
      { params: Promise.resolve({ path: ["auth", "login"] }) },
    );

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      error: "YiQiao service is temporarily unavailable.",
    });
  });

  it("preserves trailing slashes for organization and project routes", async () => {
    vi.stubEnv("API_INTERNAL_URL", "http://127.0.0.1:8888");
    const upstreamFetch = vi.fn().mockResolvedValue(
      new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", upstreamFetch);

    const projectCollectionPath = [
      "api",
      "v1",
      "orgs",
      "organizations",
      "org_default",
      "projects",
    ];
    const projectPath = [...projectCollectionPath, "project-1"];
    const organizationPath = [
      "api",
      "v1",
      "orgs",
      "organizations",
      "org_default",
    ];
    const createRequest = new NextRequest(
      "http://127.0.0.1:3000/api/backend/api/v1/orgs/organizations/org_default/projects/",
      { method: "POST", body: JSON.stringify({ name: "New project" }) },
    );
    const updateProjectRequest = new NextRequest(
      "http://127.0.0.1:3000/api/backend/api/v1/orgs/organizations/org_default/projects/project-1/",
      { method: "PATCH", body: JSON.stringify({ name: "Renamed project" }) },
    );
    const updateOrganizationRequest = new NextRequest(
      "http://127.0.0.1:3000/api/backend/api/v1/orgs/organizations/org_default/",
      {
        method: "PATCH",
        body: JSON.stringify({ name: "Renamed organization" }),
      },
    );

    await POST(createRequest, {
      params: Promise.resolve({ path: projectCollectionPath }),
    });
    await PATCH(updateProjectRequest, {
      params: Promise.resolve({ path: projectPath }),
    });
    await PATCH(updateOrganizationRequest, {
      params: Promise.resolve({ path: organizationPath }),
    });

    expect(String(upstreamFetch.mock.calls[0][0])).toBe(
      "http://127.0.0.1:8888/api/v1/orgs/organizations/org_default/projects/",
    );
    expect(String(upstreamFetch.mock.calls[1][0])).toBe(
      "http://127.0.0.1:8888/api/v1/orgs/organizations/org_default/projects/project-1/",
    );
    expect(String(upstreamFetch.mock.calls[2][0])).toBe(
      "http://127.0.0.1:8888/api/v1/orgs/organizations/org_default/",
    );
  });
});
