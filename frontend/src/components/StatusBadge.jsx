const OK_VALUES = new Set([
  "ACTIVE", "OK", "COMPLETED", "CHANGED", "UNCHANGED", "APPROVED", "VALIDATED",
  // rule dry-run verdicts
  "ELIGIBLE", "PASS",
]);
const ERROR_VALUES = new Set([
  "BROKEN", "FAILED", "ERROR", "ACCESS_BLOCKED", "NETWORK_RESTRICTED",
  "REJECTED", "CONFLICT",
  // rule dry-run verdicts
  "INELIGIBLE", "FAIL",
]);
const WARN_VALUES = new Set([
  "REQUIRES_BROWSER", "REQUIRES_MANUAL_DOWNLOAD", "NEEDS_MANUAL_CAPTURE",
  "PARTIAL", "RUNNING", "STARTED", "INACTIVE", "DISABLED", "NEEDS_REVIEW",
  "DRAFT", "SUPERSEDED", "RETIRED",
  // rule dry-run verdicts: not a pass, not a failure - the fact is missing
  "INCOMPLETE", "UNKNOWN",
]);

// Plain-language translations for technical statuses that would otherwise
// mean nothing to a non-engineer reviewer. Falls back to the raw value when
// no friendlier phrasing is defined - never hides information, just
// rephrases it.
const LABELS = {
  ACCESS_BLOCKED: "Blocked",
  NETWORK_RESTRICTED: "Blocked (network)",
  REQUIRES_BROWSER: "Needs browser check",
  REQUIRES_MANUAL_DOWNLOAD: "Needs manual download",
  NEEDS_MANUAL_CAPTURE: "Needs manual capture",
  BROKEN: "Broken",
  PARTIAL: "Needs retry (API quota)",
  FAILED: "Failed",
  RUNNING: "Running",
  STARTED: "Starting",
  INACTIVE: "Inactive",
  DISABLED: "Disabled",
  NEEDS_REVIEW: "Needs review",
  APPROVED_PENDING: "Pending activation",
  ELIGIBLE: "Eligible",
  INELIGIBLE: "Ineligible",
  INCOMPLETE: "Needs more information",
  NOT_APPLICABLE: "Not applicable",
  PASS: "Passes",
  FAIL: "Fails",
  UNKNOWN: "Unknown",
};

export default function StatusBadge({ value, label }) {
  if (!value) return <span className="muted">—</span>;

  let variant = "neutral";
  if (OK_VALUES.has(value)) variant = "ok";
  else if (ERROR_VALUES.has(value)) variant = "error";
  else if (WARN_VALUES.has(value)) variant = "warn";

  return <span className={`badge badge-${variant}`}>{label || LABELS[value] || value}</span>;
}
