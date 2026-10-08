import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { AuthProvider, useAuth } from "../AuthContext.jsx";
import * as api from "../../api/client.js";

vi.mock("../../api/client.js");

function Probe() {
  const { status } = useAuth();
  return <div data-testid="status">{status}</div>;
}

describe("AuthContext — any 401 means logout (no refresh endpoint exists)", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("logs out and clears the token when the global unauthorized handler fires", async () => {
    api.getToken.mockReturnValue("fake-token");
    api.getMe.mockResolvedValue({ role: "admin", email: "admin@test.com" });
    let capturedHandler = null;
    api.setUnauthorizedHandler.mockImplementation((fn) => {
      capturedHandler = fn;
    });

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authed"));
    expect(capturedHandler).toBeTypeOf("function");

    act(() => {
      capturedHandler();
    });

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthed"));
    expect(api.clearToken).toHaveBeenCalled();
  });

  it("clears a stale session and shows unauthed when a non-admin token is found on mount", async () => {
    api.getToken.mockReturnValue("fake-token");
    api.getMe.mockResolvedValue({ role: "applicant", email: "user@test.com" });
    api.setUnauthorizedHandler.mockImplementation(() => {});

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthed"));
    expect(api.clearToken).toHaveBeenCalled();
  });
});
