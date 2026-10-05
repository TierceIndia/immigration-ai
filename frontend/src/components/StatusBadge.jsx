const OK_VALUES = new Set(["ACTIVE", "OK", "COMPLETED", "CHANGED", "UNCHANGED"]);
const ERROR_VALUES = new Set([
  "BROKEN",
  "FAILED",
  "ERROR",
  "ACCESS_BLOCKED",
  "NETWORK_RESTRICTED",
]);
const WARN_VALUES = new Set([
  "REQUIRES_BROWSER",
  "REQUIRES_MANUAL_DOWNLOAD",
  "NEEDS_MANUAL_CAPTURE",
  "PARTIAL",
  "RUNNING",
  "STARTED",
  "INACTIVE",
]);

export default function StatusBadge({ value }) {
  if (!value) return <span className="muted">—</span>;

  let variant = "neutral";
  if (OK_VALUES.has(value)) variant = "ok";
  else if (ERROR_VALUES.has(value)) variant = "error";
  else if (WARN_VALUES.has(value)) variant = "warn";

  return <span className={`badge badge-${variant}`}>{value}</span>;
}
