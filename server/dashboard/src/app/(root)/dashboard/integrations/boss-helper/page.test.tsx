import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import BossHelperIntegrationPage from "./page";
import { BOSS_HELPER_ENDPOINTS } from "@/utils/api-endpoints";

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
  useI18n: () => ({ t: mocks.translate }),
}));

vi.mock("@/components/ui/use-toast", () => ({
  toast: mocks.toast,
}));

vi.mock("@/lib/error-message", () => ({
  getErrorMessage: (error: unknown) => String(error),
}));

function pairing(
  status: "pending" | "approved" | "connected" | "revoked",
  overrides: Record<string, unknown> = {},
) {
  return {
    pairing_id: "pairing-123",
    status,
    project_id: status === "pending" ? null : "active-project",
    scopes: ["memory:read", "memory:write", "ping"],
    key_prefix: status === "connected" ? "yqsk_example" : null,
    pairing_expires_at: "2026-07-18T10:00:00Z",
    key_expires_at: null,
    requested_at: "2026-07-18T09:00:00Z",
    approved_at: status === "pending" ? null : "2026-07-18T09:05:00Z",
    connected_at: status === "connected" ? "2026-07-18T09:06:00Z" : null,
    revoked_at: status === "revoked" ? "2026-07-18T09:07:00Z" : null,
    ...overrides,
  };
}

function renderWithCode() {
  window.history.replaceState(
    {},
    "",
    "/dashboard/integrations/boss-helper?user_code=abcd1234",
  );
  render(<BossHelperIntegrationPage />);
}

beforeEach(() => {
  mocks.apiGet.mockReset();
  mocks.apiPost.mockReset();
  mocks.getActiveProjectId.mockReset().mockReturnValue("active-project");
  mocks.toast.mockReset();
});

afterEach(() => {
  cleanup();
  window.history.replaceState({}, "", "/");
});

describe("BossHelperIntegrationPage", () => {
  it("looks up pairing status from the normalized authorization code", async () => {
    mocks.apiGet.mockResolvedValue({ data: pairing("connected") });

    renderWithCode();

    await waitFor(() =>
      expect(mocks.apiGet).toHaveBeenCalledWith(BOSS_HELPER_ENDPOINTS.STATUS, {
        params: { user_code: "ABCD-1234" },
      }),
    );
    expect(await screen.findByText("connected")).toBeTruthy();
  });

  it("approves a pending request with the active project", async () => {
    mocks.apiGet.mockResolvedValue({ data: pairing("pending") });
    mocks.apiPost.mockResolvedValue({ data: pairing("approved") });

    renderWithCode();
    fireEvent.click(
      await screen.findByRole("button", { name: "Approve connection" }),
    );

    await waitFor(() =>
      expect(mocks.apiPost).toHaveBeenCalledWith(
        BOSS_HELPER_ENDPOINTS.APPROVE,
        {
          user_code: "ABCD-1234",
          project_id: "active-project",
        },
      ),
    );
    expect(mocks.getActiveProjectId).toHaveBeenCalled();
    expect(await screen.findByText("approved")).toBeTruthy();
  });

  it("revokes the displayed pairing", async () => {
    mocks.apiGet.mockResolvedValue({ data: pairing("connected") });
    mocks.apiPost.mockResolvedValue({ data: pairing("revoked") });

    renderWithCode();
    fireEvent.click(
      await screen.findByRole("button", { name: "Revoke connection" }),
    );

    await waitFor(() =>
      expect(mocks.apiPost).toHaveBeenCalledWith(BOSS_HELPER_ENDPOINTS.REVOKE, {
        pairing_id: "pairing-123",
      }),
    );
    expect(await screen.findByText("revoked")).toBeTruthy();
  });
});
