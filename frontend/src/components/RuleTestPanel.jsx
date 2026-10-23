import { useState } from "react";
import * as api from "../api/client.js";
import StatusBadge from "./StatusBadge.jsx";

// A small, curated subset of the applicant facts the rule engine can use.
// Anything left blank is omitted from the profile, so a rule that needs it
// comes back UNKNOWN - which is exactly what an incomplete real application
// would do.
const TEST_FIELDS = [
  { key: "age", label: "Age", type: "number" },
  {
    key: "english_level",
    label: "English level",
    type: "select",
    options: ["", "none", "vocational", "competent", "proficient", "superior"],
  },
  { key: "points", label: "Points", type: "number" },
  { key: "occupation", label: "Occupation (ANZSCO)", type: "text" },
  { key: "work_experience_years", label: "Work experience (years)", type: "number" },
  { key: "visa_history", label: "Visa history", type: "text" },
  {
    key: "employer_sponsored",
    label: "Employer sponsored",
    type: "select",
    options: ["", "true", "false"],
  },
  {
    key: "in_australia_at_application",
    label: "In Australia when applying",
    type: "select",
    options: ["", "true", "false"],
  },
  {
    key: "has_health_insurance",
    label: "Health insurance",
    type: "select",
    options: ["", "true", "false"],
  },
  {
    key: "genuine_temporary_entrant",
    label: "Genuine temporary entrant",
    type: "select",
    options: ["", "true", "false"],
  },
];

const BOOLEAN_FIELDS = new Set(
  TEST_FIELDS.filter((f) => f.type === "select" && f.options[1] === "true").map((f) => f.key)
);

function buildProfile(values) {
  const profile = {};
  for (const field of TEST_FIELDS) {
    const raw = values[field.key];
    if (raw === undefined || raw === "") continue;
    if (field.type === "number") {
      const n = Number(raw);
      if (Number.isFinite(n)) profile[field.key] = n;
    } else if (BOOLEAN_FIELDS.has(field.key)) {
      profile[field.key] = raw === "true";
    } else {
      profile[field.key] = raw;
    }
  }
  return profile;
}

export default function RuleTestPanel({ programId, ruleIds, onClose }) {
  const [program, setProgram] = useState(programId || "");
  const [values, setValues] = useState({});
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const checkingOne = Array.isArray(ruleIds) && ruleIds.length > 0;

  function setField(key, value) {
    setValues((v) => ({ ...v, [key]: value }));
  }

  async function run(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = {
        program_id: program.trim(),
        profile: buildProfile(values),
      };
      if (checkingOne) body.rule_ids = ruleIds;
      setReport(await api.evaluateRules(body));
    } catch (err) {
      setReport(null);
      setError(err.message || "The check could not run");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-label="Test rules">
      <div className="flex-between">
        <h2>Test rules</h2>
        {onClose && (
          <button className="btn btn-sm" onClick={onClose}>
            Close
          </button>
        )}
      </div>
      <p className="muted">
        {checkingOne
          ? `Checking ${ruleIds.length} selected rule${ruleIds.length === 1 ? "" : "s"} against an applicant you describe.`
          : "Checking the program's active rules against an applicant you describe."}{" "}
        Nothing is saved and nothing is activated.
      </p>

      <form onSubmit={run}>
        <div className="form-row">
          <div className="field">
            <label htmlFor="test-program">Program</label>
            <input
              id="test-program"
              value={program}
              onChange={(e) => setProgram(e.target.value.toUpperCase())}
              placeholder="e.g. SKILLED_189"
              required
            />
          </div>
        </div>
        <div className="form-grid">
          {TEST_FIELDS.map((field) => (
            <div className="field" key={field.key}>
              <label htmlFor={`test-${field.key}`}>{field.label}</label>
              {field.type === "select" ? (
                <select
                  id={`test-${field.key}`}
                  value={values[field.key] ?? ""}
                  onChange={(e) => setField(field.key, e.target.value)}
                >
                  {field.options.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt === "" ? "Not given" : opt === "true" ? "Yes" : opt === "false" ? "No" : opt}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  id={`test-${field.key}`}
                  type={field.type}
                  value={values[field.key] ?? ""}
                  onChange={(e) => setField(field.key, e.target.value)}
                />
              )}
            </div>
          ))}
        </div>
        <div className="flex">
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? "Checking…" : "Run check"}
          </button>
        </div>
      </form>

      {error && <p className="field-error">{error}</p>}

      {report && (
        <div className="rule-test-report">
          <h3>
            Verdict: <StatusBadge value={report.business_status} />
          </h3>
          <p className="muted">
            {report.total_rules} rule{report.total_rules === 1 ? "" : "s"} checked:{" "}
            {report.passed} passed, {report.failed} failed, {report.unknown} unknown
            {report.not_applicable ? `, ${report.not_applicable} not applicable` : ""}. Ruleset{" "}
            <code>{String(report.ruleset_hash).slice(0, 12)}</code>
          </p>

          {report.results.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Rule</th>
                    <th>Status</th>
                    <th>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {report.results.map((r) => (
                    <tr key={r.rule_id}>
                      <td>
                        <code>{r.rule_id}</code>
                      </td>
                      <td>
                        <StatusBadge value={r.status} />
                      </td>
                      <td>{r.explanation || r.message || r.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {report.gaps.length > 0 && (
            <div>
              <h4>Still to ask about</h4>
              <ul>
                {report.gaps.map((g) => (
                  <li key={g.field}>
                    {g.question} <span className="muted">({g.field})</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
