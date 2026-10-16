import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ReviewPage from "../ReviewPage.jsx";
import { ToastProvider } from "../../components/ToastContext.jsx";
import * as api from "../../api/client.js";

vi.mock("../../api/client.js");

const SAMPLE_REQUIREMENT = {
  requirement_id: "REQ-AGE-1",
  category: "AGE",
  name: "Age requirement",
  description: "Applicants must be under 45.",
  field: "applicant.age",
  concept: null,
  evaluable: true,
  operator: "LT",
  value: 45,
  unit: "years",
  mandatory: true,
  confidence: 0.9,
  status: "NEEDS_REVIEW",
  current_version: 1,
  evidence: [{ chunk_id: 1, evidence_text: "Applicants must be under 45 years of age.", evidence_type: "DIRECT_TEXT" }],
  source: { source_id: "SRC-AU-189", authority: "Home Affairs", document_id: "DOC-1", document_title: "Eligibility", url: "https://example.gov/189" },
  document_version: 1,
  previous_value: null,
  program_id: "SKILLED_189",
};

function renderPage() {
  return render(
    <ToastProvider>
      <ReviewPage />
    </ToastProvider>
  );
}

describe("ReviewPage approve flow", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.getReviewQueue.mockResolvedValue({
      requirements: [SAMPLE_REQUIREMENT],
      status: "NEEDS_REVIEW",
      program_id: null,
      count: 1,
    });
  });

  it("shows the requirement with its evidence and approves it on click", async () => {
    const user = userEvent.setup();
    api.approveRequirement.mockResolvedValue({ requirement_id: "REQ-AGE-1", status: "APPROVED" });

    renderPage();

    expect(await screen.findByText("Age requirement")).toBeInTheDocument();
    expect(screen.getByText(/applicants must be under 45 years of age/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^approve$/i }));

    await waitFor(() => expect(api.approveRequirement).toHaveBeenCalledWith("REQ-AGE-1"));
    // The queue is reloaded after a successful action.
    await waitFor(() => expect(api.getReviewQueue).toHaveBeenCalledTimes(2));
  });

  it("supports multi-select with a confirm dialog stating the count", async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Age requirement");
    await user.click(screen.getByRole("checkbox", { name: /select age requirement/i }));

    expect(screen.getByText("1 selected")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /approve selected/i }));

    expect(await screen.findByText(/approve 1 requirement/i)).toBeInTheDocument();
  });

  it("shows an Evaluable badge for a requirement with a real field", async () => {
    renderPage();
    await screen.findByText("Age requirement");
    expect(screen.getByText("Evaluable")).toBeInTheDocument();
  });

  it("labels confidence as model-reported, low signal", async () => {
    renderPage();
    await screen.findByText("Age requirement");
    expect(screen.getByText(/model-reported, low signal/i)).toBeInTheDocument();
  });

  it("defaults to evaluable_only=true on the initial load", async () => {
    renderPage();
    await screen.findByText("Age requirement");
    expect(api.getReviewQueue).toHaveBeenCalledWith(
      expect.objectContaining({ evaluableOnly: true })
    );
  });
});

describe("ReviewPage evaluable-only toggle", () => {
  const NOT_MODELED_REQUIREMENT = {
    ...SAMPLE_REQUIREMENT,
    requirement_id: "REQ-VISA-HISTORY-1",
    name: "Bridging visa grant limit",
    field: "NOT_MODELED",
    concept: "visa_history_bridging_count",
    evaluable: false,
  };

  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("hides a not-modeled requirement by default, reveals it when toggled off", async () => {
    const user = userEvent.setup();
    api.getReviewQueue.mockImplementation(({ evaluableOnly } = {}) =>
      Promise.resolve({
        requirements: evaluableOnly ? [] : [NOT_MODELED_REQUIREMENT],
        status: "NEEDS_REVIEW",
        program_id: null,
        evaluable_only: evaluableOnly,
        count: evaluableOnly ? 0 : 1,
      })
    );

    renderPage();
    expect(await screen.findByText(/queue is clear/i)).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: /evaluable only/i }));

    expect(await screen.findByText("Bridging visa grant limit")).toBeInTheDocument();
    expect(screen.getByText(/not modeled: visa_history_bridging_count/i)).toBeInTheDocument();
  });
});
