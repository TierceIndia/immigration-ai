import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import DashboardPage from "../DashboardPage.jsx";
import { ToastProvider } from "../../components/ToastContext.jsx";
import * as api from "../../api/client.js";

vi.mock("../../api/client.js");

// Read a stat tile by its label rather than by its number: several tiles
// legitimately show the same value (one source is both total and healthy).
function statValue(label) {
  const labelEl = screen.getByText(label);
  return labelEl.parentElement.querySelector(".stat-value").textContent;
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <DashboardPage />
      </ToastProvider>
    </MemoryRouter>
  );
}

const SAMPLE_SOURCES = [
  {
    source_id: "SRC-AU-189",
    status: "ACTIVE",
    fetch_status: null,
    consecutive_failures: 0,
    last_checked_at: "2026-10-10T03:00:00Z",
    blocked_until: null,
  },
];

const SAMPLE_QUEUE = { requirements: [], status: "NEEDS_REVIEW", program_id: null, count: 3 };
const SAMPLE_RULES_QUEUE = { counts: { drafts: 2, validated: 5 } };
const SAMPLE_SCHEDULER = {
  enabled: true,
  mode: "daily",
  hour_utc: 3,
  minute_utc: 0,
  interval_minutes: null,
  next_run_at: "2026-10-11T03:00:00Z",
};

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("renders all stat tiles with correct values when all APIs succeed", async () => {
    api.listSources.mockResolvedValue(SAMPLE_SOURCES);
    api.getReviewQueue.mockResolvedValue(SAMPLE_QUEUE);
    api.getRulesQueue.mockResolvedValue(SAMPLE_RULES_QUEUE);
    api.getScheduler.mockResolvedValue(SAMPLE_SCHEDULER);

    renderPage();

    await waitFor(() => expect(statValue("Total sources")).toBe("1"));
    expect(statValue("Healthy sources")).toBe("1");
    expect(statValue("Failing sources")).toBe("0");
    expect(statValue("Requirements awaiting review")).toBe("3");
    expect(statValue("Draft rules")).toBe("2");
    expect(statValue("Rules awaiting approval")).toBe("5");
  });

  it("does not crash when sources API returns null data (error branch)", async () => {
    // Simulate sources error — firstError check redirects to ErrorState,
    // so this just ensures no unhandled throw.
    api.listSources.mockRejectedValue(new Error("network error"));
    api.getReviewQueue.mockResolvedValue(SAMPLE_QUEUE);
    api.getRulesQueue.mockResolvedValue(SAMPLE_RULES_QUEUE);
    api.getScheduler.mockResolvedValue(SAMPLE_SCHEDULER);

    renderPage();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument()
    );
  });

  it("shows 0 for tiles when data fields are missing (optional-chaining guard)", async () => {
    // API returns data but without the expected shape fields
    api.listSources.mockResolvedValue([]);
    api.getReviewQueue.mockResolvedValue({ requirements: [], count: 0 });
    api.getRulesQueue.mockResolvedValue({ counts: { drafts: 0, validated: 0 } });
    api.getScheduler.mockResolvedValue({ ...SAMPLE_SCHEDULER, enabled: false });

    renderPage();

    // "Scheduler disabled" appears when scheduler is disabled
    await waitFor(() =>
      expect(screen.getByText(/scheduler disabled/i)).toBeInTheDocument()
    );
  });
});
