import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import DocumentsPage from "../DocumentsPage.jsx";
import * as api from "../../api/client.js";

vi.mock("../../api/client.js");

function document(overrides = {}) {
  return {
    document_id: "DOC-AU-189",
    source_id: "SRC-AU-189",
    program_id: "SKILLED_189",
    title: "Subclass 189 eligibility",
    status: "CURRENT",
    ...overrides,
  };
}

function hit(overrides = {}) {
  return {
    chunk_id: "CHUNK-1",
    document_id: "DOC-AU-189",
    program_id: "SKILLED_189",
    title: "Subclass 189 eligibility",
    heading_path: "Eligibility > English",
    page_number: 3,
    snippet: "You must have at least competent English.",
    similarity: 0.91,
    citation: "Subclass 189 eligibility, p. 3",
    mode: "semantic",
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <DocumentsPage />
    </MemoryRouter>
  );
}

describe("DocumentsPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.listDocuments.mockResolvedValue([document()]);
  });

  it("browses stored documents without searching anything", async () => {
    renderPage();

    expect(await screen.findByText("Subclass 189 eligibility")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View details" })).toBeInTheDocument();
    expect(api.searchDocuments).not.toHaveBeenCalled();
  });

  it("searches the stored text and says the match was by meaning", async () => {
    const user = userEvent.setup();
    api.searchDocuments.mockResolvedValue({
      query: "english requirement",
      mode: "semantic",
      semantic_available: true,
      embedded_chunks: 120,
      total: 1,
      results: [hit()],
    });

    renderPage();

    await waitFor(() => expect(api.listDocuments).toHaveBeenCalled());
    await user.type(screen.getByLabelText("Search the stored text"), "english requirement");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText(/1 result for/)).toBeInTheDocument();
    expect(api.searchDocuments).toHaveBeenCalledWith({
      query: "english requirement",
      mode: "auto",
      limit: 20,
    });
    expect(screen.getByText("Matched by meaning")).toBeInTheDocument();
    expect(
      screen.getByText("You must have at least competent English.")
    ).toBeInTheDocument();
    expect(screen.getByText("Subclass 189 eligibility, p. 3")).toBeInTheDocument();
    // The browse list gives way to the results, so the document card - and
    // its "View details" control - is no longer on screen.
    expect(screen.queryByRole("button", { name: "View details" })).not.toBeInTheDocument();
  });

  it("is honest when meaning-based search is off and words were matched instead", async () => {
    const user = userEvent.setup();
    api.searchDocuments.mockResolvedValue({
      query: "english",
      mode: "keyword",
      semantic_available: false,
      embedded_chunks: 0,
      total: 1,
      results: [hit({ mode: "keyword", similarity: null })],
    });

    renderPage();
    await user.type(screen.getByLabelText("Search the stored text"), "english");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("Matched by keyword")).toBeInTheDocument();
    expect(screen.getByText(/Meaning-based search is off/i)).toBeInTheDocument();
  });

  it("explains a refused meaning-only search instead of offering a pointless retry", async () => {
    const user = userEvent.setup();
    api.searchDocuments.mockRejectedValue({
      status: 409,
      message: "Embeddings are off. Set EMBEDDINGS_ENABLED=true",
    });

    renderPage();
    await user.selectOptions(screen.getByLabelText("How to match"), "semantic");
    await user.type(screen.getByLabelText("Search the stored text"), "english");
    await user.click(screen.getByRole("button", { name: "Search" }));

    expect(await screen.findByText("Search unavailable")).toBeInTheDocument();
    expect(screen.getByText(/Embeddings are off/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try a keyword search" })).toBeInTheDocument();
  });
});
