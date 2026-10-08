import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SchedulerPage from "../SchedulerPage.jsx";
import { ToastProvider } from "../../components/ToastContext.jsx";
import * as api from "../../api/client.js";

vi.mock("../../api/client.js");

function renderPage() {
  return render(
    <ToastProvider>
      <SchedulerPage />
    </ToastProvider>
  );
}

describe("SchedulerPage form validation", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.getScheduler.mockResolvedValue({
      enabled: true,
      mode: "daily",
      hour_utc: 3,
      minute_utc: 0,
      interval_minutes: null,
      next_run_at: "2026-10-09T03:00:00+00:00",
    });
  });

  it("rejects an out-of-range hour and never calls the API", async () => {
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByLabelText(/hour \(utc\)/i)).toBeInTheDocument());

    const hourInput = screen.getByLabelText(/hour \(utc\)/i);
    await user.clear(hourInput);
    await user.type(hourInput, "25");
    await user.click(screen.getByRole("button", { name: /save schedule/i }));

    expect(await screen.findByText(/hour must be 0-23/i)).toBeInTheDocument();
    expect(api.updateScheduler).not.toHaveBeenCalled();
  });

  it("rejects an interval below 15 minutes", async () => {
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByLabelText(/^mode$/i)).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText(/^mode$/i), "interval");

    const intervalInput = screen.getByLabelText(/interval \(minutes\)/i);
    await user.clear(intervalInput);
    await user.type(intervalInput, "5");
    await user.click(screen.getByRole("button", { name: /save schedule/i }));

    expect(await screen.findByText(/at least 15 minutes/i)).toBeInTheDocument();
    expect(api.updateScheduler).not.toHaveBeenCalled();
  });

  it("asks for confirmation and saves when the schedule is valid", async () => {
    const user = userEvent.setup();
    api.updateScheduler.mockResolvedValue({
      enabled: true,
      mode: "daily",
      hour_utc: 9,
      minute_utc: 15,
      interval_minutes: null,
      next_run_at: "2026-10-09T09:15:00+00:00",
    });
    renderPage();

    await waitFor(() => expect(screen.getByLabelText(/hour \(utc\)/i)).toBeInTheDocument());
    const hourInput = screen.getByLabelText(/hour \(utc\)/i);
    await user.clear(hourInput);
    await user.type(hourInput, "9");
    await user.click(screen.getByRole("button", { name: /save schedule/i }));

    expect(await screen.findByText(/change the sync schedule/i)).toBeInTheDocument();
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /save schedule/i }));

    await waitFor(() => expect(api.updateScheduler).toHaveBeenCalled());
  });
});
