import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import AdminRoute from "../AdminRoute.jsx";
import { useAuth } from "../AuthContext.jsx";

vi.mock("../AuthContext.jsx", () => ({
  useAuth: vi.fn(),
}));

function renderWithStatus(status) {
  useAuth.mockReturnValue({ status });
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/login" element={<div>Login Page</div>} />
        <Route
          path="/"
          element={
            <AdminRoute>
              <div>Secret Admin Content</div>
            </AdminRoute>
          }
        />
      </Routes>
    </MemoryRouter>
  );
}

describe("AdminRoute", () => {
  it("renders nothing while the session is still being checked", () => {
    renderWithStatus("loading");
    expect(screen.queryByText("Secret Admin Content")).not.toBeInTheDocument();
    expect(screen.queryByText("Login Page")).not.toBeInTheDocument();
  });

  it("blocks and redirects to /login when not an authenticated admin", () => {
    renderWithStatus("unauthed");
    expect(screen.getByText("Login Page")).toBeInTheDocument();
    expect(screen.queryByText("Secret Admin Content")).not.toBeInTheDocument();
  });

  it("renders the protected content once confirmed admin", () => {
    renderWithStatus("authed");
    expect(screen.getByText("Secret Admin Content")).toBeInTheDocument();
  });
});
