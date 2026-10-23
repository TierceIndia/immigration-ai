import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { openPage } from "./helpers.js";

const RULE = {
  rule_id: "RULE-REQ-1",
  program_id: "SKILLED_189",
  category: "ELIGIBILITY",
  field: "age",
  operator: "between",
  value: [18, 45],
  unit: "years",
  mandatory: true,
  critical: false,
  evidence_text: "You must be between 18 and 45.",
  status: "APPROVED",
  evaluable: true,
};

const OPAQUE_RULE = {
  ...RULE,
  rule_id: "RULE-REQ-2",
  field: "minister_satisfied",
  evidence_text: "The Minister must be satisfied that the applicant meets the criteria.",
  evaluable: false,
};

const REPORT = {
  program_id: "SKILLED_189",
  overall_status: "PASS",
  business_status: "ELIGIBLE",
  total_rules: 2,
  passed: 2,
  failed: 0,
  unknown: 0,
  not_applicable: 0,
  ruleset_hash: "0123456789abcdef",
  evaluated_at: "2026-10-10T03:00:00+00:00",
  results: [
    {
      rule_id: "RULE-REQ-1",
      status: "PASS",
      explanation: "Age 30 is between 18 and 45",
      field: "age",
    },
  ],
  gaps: [],
};

test("the dashboard shows the numbers the API reports", async ({ page }) => {
  await openPage(page, "/", {
    "GET /api/v1/admin/rules/queue": { counts: { drafts: 2, validated: 5 } },
  });

  const tile = (label) =>
    page.locator(".stat-tile", { has: page.locator(".stat-label", { hasText: label }) });

  await expect(tile("Total sources").locator(".stat-value")).toHaveText("1");
  await expect(tile("Healthy sources").locator(".stat-value")).toHaveText("1");
  await expect(tile("Rules awaiting approval").locator(".stat-value")).toHaveText("5");
  await expect(page.getByText("Sync status")).toBeVisible();
});

test("an unanswerable rule is badged and can be dry-run before activation", async ({ page }) => {
  await openPage(page, "/rules", {
    "GET /api/v1/admin/rules": [RULE, OPAQUE_RULE],
    "POST /api/v1/admin/rules/evaluate": REPORT,
  });

  const rows = page.locator("tbody tr");
  await expect(rows).toHaveCount(2);
  await expect(
    page.getByText("Not evaluable - no applicant field")
  ).toHaveCount(1);

  const opaqueRow = rows.filter({ hasText: "minister_satisfied" });
  await opaqueRow.getByRole("button", { name: "Test" }).click();

  const panel = page.getByRole("region", { name: "Test rules" });
  await expect(panel).toBeVisible();
  await panel.getByRole("button", { name: "Run check" }).click();

  await expect(panel.getByText("Eligible")).toBeVisible();
  await expect(panel.getByText("Age 30 is between 18 and 45")).toBeVisible();
  // A dry run writes nothing.
  await expect(panel.getByText(/Nothing is saved/i)).toBeVisible();
});

test("activating an unanswerable rule needs the typed confirmation", async ({ page }) => {
  await openPage(page, "/rules", {
    "GET /api/v1/admin/rules": [OPAQUE_RULE],
    "POST /api/v1/admin/rules/RULE-REQ-2/activate": { ...OPAQUE_RULE, status: "ACTIVE" },
  });

  await page.getByRole("button", { name: "Activate" }).click();
  const dialog = page.getByRole("dialog");
  const confirm = dialog.getByRole("button", { name: "Activate" });
  await expect(confirm).toBeDisabled();

  await dialog.getByLabel("Type ACTIVATE to confirm").fill("ACTIVATE");
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(dialog).toBeHidden();
});

test("the dashboard has no serious accessibility violations", async ({ page }) => {
  await openPage(page, "/");

  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical"
  );
  expect(
    serious.map((v) => `${v.id}: ${v.nodes.length} node(s)`),
    JSON.stringify(serious, null, 2)
  ).toEqual([]);
});

test("the rules page has no serious accessibility violations", async ({ page }) => {
  await openPage(page, "/rules", { "GET /api/v1/admin/rules": [RULE, OPAQUE_RULE] });

  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter(
    (v) => v.impact === "serious" || v.impact === "critical"
  );
  expect(
    serious.map((v) => `${v.id}: ${v.nodes.length} node(s)`),
    JSON.stringify(serious, null, 2)
  ).toEqual([]);
});
