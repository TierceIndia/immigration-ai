import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RuleTestPanel from "../RuleTestPanel.jsx";
import * as api from "../../api/client.js";

vi.mock("../../api/client.js");

const REPORT = {
  program_id: "SKILLED_189",
  overall_status: "FAIL",
  business_status: "INELIGIBLE",
  total_rules: 2,
  passed: 1,
  failed: 1,
  unknown: 0,
  not_applicable: 0,
  ruleset_hash: "abcdef1234567890",
  evaluated_at: "2026-10-10T03:00:00+00:00",
  results: [
    {
      rule_id: "RULE-1",
      status: "PASS",
      explanation: "Age 30 is between 18 and 45",
      field: "age",
      mandatory: true,
      critical: false,
      evaluable: true,
    },
    {
      rule_id: "RULE-2",
      status: "FAIL",
      explanation: "Points 50 is below the required 65",
      field: "points",
      mandatory: true,
      critical: true,
      evaluable: true,
    },
  ],
  gaps: [{ field: "english_level", question: "What is your English level?", rule_ids: [], source_evidence: null }],
};

describe("RuleTestPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("sends the typed-only facts and the selected rules to the dry run", async () => {
    const user = userEvent.setup();
    api.evaluateRules.mockResolvedValue(REPORT);

    render(<RuleTestPanel programId="SKILLED_189" ruleIds={["RULE-1"]} />);

    await user.clear(screen.getByLabelText("Age"));
    await user.type(screen.getByLabelText("Age"), "30");
    await user.selectOptions(screen.getByLabelText("Employer sponsored"), "true");
    await user.click(screen.getByRole("button", { name: "Run check" }));

    await waitFor(() => expect(api.evaluateRules).toHaveBeenCalledTimes(1));
    expect(api.evaluateRules).toHaveBeenCalledWith({
      program_id: "SKILLED_189",
      profile: { age: 30, employer_sponsored: true },
      rule_ids: ["RULE-1"],
    });
  });

  it("shows the verdict, each rule's result and the gaps still to ask about", async () => {
    const user = userEvent.setup();
    api.evaluateRules.mockResolvedValue(REPORT);

    render(<RuleTestPanel programId="SKILLED_189" />);
    await user.click(screen.getByRole("button", { name: "Run check" }));

    expect(await screen.findByText("Ineligible")).toBeInTheDocument();
    expect(screen.getByText("RULE-1")).toBeInTheDocument();
    expect(screen.getByText("Age 30 is between 18 and 45")).toBeInTheDocument();
    expect(screen.getByText("Points 50 is below the required 65")).toBeInTheDocument();
    expect(screen.getByText(/What is your English level\?/)).toBeInTheDocument();
    expect(screen.getByText("abcdef123456")).toBeInTheDocument();
  });

  it("says what went wrong instead of showing a stale report", async () => {
    const user = userEvent.setup();
    api.evaluateRules.mockRejectedValue(
      new Error("No rules to check for program SKILLED_189")
    );

    render(<RuleTestPanel programId="SKILLED_189" />);
    await user.click(screen.getByRole("button", { name: "Run check" }));

    expect(
      await screen.findByText("No rules to check for program SKILLED_189")
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Verdict/ })).not.toBeInTheDocument();
  });
});
