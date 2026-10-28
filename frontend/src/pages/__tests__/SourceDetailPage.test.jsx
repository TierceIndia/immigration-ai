import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import SourceDetailPage from "../SourceDetailPage.jsx";
import { ToastProvider } from "../../components/ToastContext.jsx";
import * as api from "../../api/client.js";

vi.mock("../../api/client.js");

const SOURCE = {
  source_id: "SRC-AU-189",
  program_id: "SKILLED_189",
  visa_category: "SKILLED",
  authority: "Home Affairs",
  status: "ACTIVE",
  fetch_status: "OK",
  url: "https://example.gov/189",
  last_error: null,
};

function reqFor(sourceId, requirementId) {
  return {
    requirement_id: requirementId,
    name: `Requirement ${requirementId}`,
    category: "AGE",
    field: "applicant.age",
    operator: "LT",
    value: 45,
    status: "NEEDS_REVIEW",
    source: sourceId ? { source_id: sourceId, authority: "Home Affairs" } : null,
  };
}

function renderPage() {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/sources/SRC-AU-189"]}>
        <Routes>
          <Route path="/sources/:sourceId" element={<SourceDetailPage />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>
  );
}

describe("SourceDetailPage — exact provenance join, not program_id approximation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.getSource.mockResolvedValue(SOURCE);
    api.listDocuments.mockResolvedValue([]);
  });

  it("shows only requirements whose resolved source matches this source, with an unattributed note", async () => {
    // Four statuses are queried in parallel; only NEEDS_REVIEW has data here.
    api.getReviewQueue.mockImplementation(({ status }) => {
      if (status !== "NEEDS_REVIEW") return Promise.resolve({ requirements: [] });
      return Promise.resolve({
        requirements: [
          reqFor("SRC-AU-189", "REQ-OWN"), // this source
          reqFor("SRC-AU-189-POINTS", "REQ-SIBLING"), // sibling source, same program
          reqFor(null, "REQ-UNATTRIBUTED"), // no document_version_id to resolve
        ],
      });
    });

    renderPage();

    expect(await screen.findByText("Requirement REQ-OWN")).toBeInTheDocument();
    expect(screen.queryByText("Requirement REQ-SIBLING")).not.toBeInTheDocument();
    expect(screen.queryByText("Requirement REQ-UNATTRIBUTED")).not.toBeInTheDocument();
    expect(screen.getByText(/1 requirement in this program/i)).toBeInTheDocument();
  });

  it("shows no unattributed note when every requirement resolves cleanly", async () => {
    api.getReviewQueue.mockImplementation(({ status }) => {
      if (status !== "NEEDS_REVIEW") return Promise.resolve({ requirements: [] });
      return Promise.resolve({ requirements: [reqFor("SRC-AU-189", "REQ-OWN")] });
    });

    renderPage();

    await waitFor(() => expect(screen.getByText("Requirement REQ-OWN")).toBeInTheDocument());
    expect(screen.queryByText(/no document link/i)).not.toBeInTheDocument();
  });
});
