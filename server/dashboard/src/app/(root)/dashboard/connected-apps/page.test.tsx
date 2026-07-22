import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ConnectedAppsPage from "./page";
import { OAUTH_ENDPOINTS } from "@/utils/api-endpoints";

const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  getActiveProjectId: vi.fn(),
  toast: vi.fn(),
  translate: (value: string) => value,
}));

vi.mock("@/utils/api", () => ({
  api: {
    get: mocks.apiGet,
    post: mocks.apiPost,
  },
  getActiveProjectId: mocks.getActiveProjectId,
}));

vi.mock("@/lib/i18n", () => ({
  useI18n: () => ({ t: mocks.translate, language: "en" }),
}));

vi.mock("@/components/ui/use-toast", () => ({
  toast: mocks.toast,
}));

vi.mock("@/lib/error-message", () => ({
  getErrorMessage: (error: unknown, fallback?: string) =>
    error instanceof Error ? error.message : fallback || String(error),
}));

function deviceRequest(
  status: "pending" | "approved" | "denied",
  approvedScopes: string[] = [],
) {
  return {
    id: "request-123",
    client_id: "example-public-app",
    application_name: "Example Public App",
    audience: "api",
    requested_scopes: ["memory:read", "memory:write"],
    approved_scopes: approvedScopes,
    status,
    project_id: status === "approved" ? "active-project" : null,
    expires_at: "2026-07-22T10:00:00Z",
    created_at: "2026-07-22T09:00:00Z",
  };
}

function grant() {
  return {
    id: "grant-123",
    client_id: "example-public-app",
    application_name: "Example Public App",
    audience: "api",
    scopes: ["memory:read"],
    project_id: "active-project",
    status: "active",
    access_expires_at: "2026-07-22T10:00:00Z",
    refresh_expires_at: "2026-08-22T10:00:00Z",
    last_used_at: null,
    created_at: "2026-07-22T09:00:00Z",
    revoked_at: null,
    is_owner: true,
    owner_email: "owner@example.com",
  };
}

const emptyGrantResponse = {
  items: [],
  audit_events: [],
  can_manage_project: false,
};

function configureGet(grantItems: ReturnType<typeof grant>[] = []) {
  mocks.apiGet.mockImplementation((url: string) => {
    if (url === OAUTH_ENDPOINTS.GRANTS) {
      return Promise.resolve({
        data: { ...emptyGrantResponse, items: grantItems },
      });
    }
    if (url === OAUTH_ENDPOINTS.APPLICATIONS) {
      return Promise.resolve({ data: { items: [], can_register: true } });
    }
    return Promise.reject(new Error(`Unexpected GET ${url}`));
  });
}

function renderWithCode() {
  window.history.replaceState(
    {},
    "",
    "/dashboard/connected-apps?view=authorize&user_code=abcd1234",
  );
  render(<ConnectedAppsPage />);
}

function activateTab(name: string) {
  fireEvent.mouseDown(screen.getByRole("tab", { name }), {
    button: 0,
    ctrlKey: false,
  });
}

beforeEach(() => {
  mocks.apiGet.mockReset();
  mocks.apiPost.mockReset();
  mocks.getActiveProjectId.mockReset().mockReturnValue("active-project");
  mocks.toast.mockReset();
  configureGet();
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

describe("ConnectedAppsPage", () => {
  it("scrubs the URL and looks up the request with a POST body", async () => {
    mocks.apiPost.mockResolvedValue({ data: deviceRequest("pending") });

    renderWithCode();

    expect(window.location.search).toBe("?view=authorize");
    await waitFor(() =>
      expect(mocks.apiPost).toHaveBeenCalledWith(
        OAUTH_ENDPOINTS.DEVICE_LOOKUP,
        { user_code: "ABCD-1234" },
      ),
    );
    expect(await screen.findByText("Example Public App")).toBeTruthy();
  });

  it("approves only the selected subset of requested scopes", async () => {
    mocks.apiPost.mockImplementation((url: string) => {
      if (url === OAUTH_ENDPOINTS.DEVICE_LOOKUP) {
        return Promise.resolve({ data: deviceRequest("pending") });
      }
      if (url === OAUTH_ENDPOINTS.DEVICE_APPROVE("request-123")) {
        return Promise.resolve({
          data: deviceRequest("approved", ["memory:read"]),
        });
      }
      return Promise.reject(new Error(`Unexpected POST ${url}`));
    });

    renderWithCode();
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "memory:write" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Approve selected scopes" }),
    );

    await waitFor(() =>
      expect(mocks.apiPost).toHaveBeenCalledWith(
        OAUTH_ENDPOINTS.DEVICE_APPROVE("request-123"),
        {
          project_id: "active-project",
          approved_scopes: ["memory:read"],
        },
      ),
    );
  });

  it("rejects a pending authorization request by request id", async () => {
    mocks.apiPost.mockImplementation((url: string) => {
      if (url === OAUTH_ENDPOINTS.DEVICE_LOOKUP) {
        return Promise.resolve({ data: deviceRequest("pending") });
      }
      if (url === OAUTH_ENDPOINTS.DEVICE_REJECT("request-123")) {
        return Promise.resolve({ data: deviceRequest("denied") });
      }
      return Promise.reject(new Error(`Unexpected POST ${url}`));
    });

    renderWithCode();
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Reject connection" }),
    );

    await waitFor(() =>
      expect(mocks.apiPost).toHaveBeenCalledWith(
        OAUTH_ENDPOINTS.DEVICE_REJECT("request-123"),
      ),
    );
  });

  it("revokes an individual grant", async () => {
    configureGet([grant()]);
    mocks.apiPost.mockResolvedValue({ data: {} });
    render(<ConnectedAppsPage />);

    activateTab("Connections");
    fireEvent.click(await screen.findByRole("button", { name: "Revoke" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Revoke connection" }),
    );

    await waitFor(() =>
      expect(mocks.apiPost).toHaveBeenCalledWith(
        OAUTH_ENDPOINTS.GRANT_REVOKE("grant-123"),
      ),
    );
  });

  it("registers a public application with normalized lists", async () => {
    mocks.apiPost.mockResolvedValue({ data: {} });
    render(<ConnectedAppsPage />);

    activateTab("Applications");
    fireEvent.change(await screen.findByLabelText("Client ID"), {
      target: { value: "notes-app" },
    });
    fireEvent.change(screen.getByLabelText("Display name"), {
      target: { value: "Notes App" },
    });
    fireEvent.change(screen.getByLabelText("Allowed scopes"), {
      target: { value: "memory:read, memory:read, memory:write" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Register application" }),
    );

    await waitFor(() =>
      expect(mocks.apiPost).toHaveBeenCalledWith(OAUTH_ENDPOINTS.APPLICATIONS, {
        client_id: "notes-app",
        display_name: "Notes App",
        client_type: "public",
        allowed_audiences: ["yiqiao:memory-api"],
        allowed_scopes: ["memory:read", "memory:write"],
      }),
    );
  });
});
