import { useParams } from "react-router-dom";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import StatusBadge from "../components/StatusBadge.jsx";
import DataTable from "../components/DataTable.jsx";
import ErrorState from "../components/ErrorState.jsx";

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function ApplicantDetailPage() {
  const { applicantId } = useParams();
  const state = useApi(() => api.getApplicant(applicantId), [applicantId]);

  if (state.loading) return <p className="muted">Loading…</p>;
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;

  const a = state.data;
  const columns = [
    { key: "program_id", header: "Program" },
    { key: "business_status", header: "Result", render: (r) => <StatusBadge value={r.business_status} /> },
    { key: "ruleset_hash", header: "Ruleset hash", render: (r) => <span className="faint">{r.ruleset_hash?.slice(0, 12) ?? "—"}</span> },
    { key: "evaluated_at", header: "Date", render: (r) => formatDateTime(r.evaluated_at) },
  ];

  return (
    <div>
      <h1>{[a.first_name, a.last_name].filter(Boolean).join(" ") || a.applicant_id}</h1>
      <div className="card">
        <div className="card-grid">
          <div>
            <div className="muted">Owner email</div>
            <div>{a.owner_email}</div>
          </div>
          <div>
            <div className="muted">Age</div>
            <div>{a.age ?? "—"}</div>
          </div>
          <div>
            <div className="muted">English level</div>
            <div>{a.english_level ?? "—"}</div>
          </div>
          <div>
            <div className="muted">Points</div>
            <div>{a.points ?? "—"}</div>
          </div>
          <div>
            <div className="muted">Occupation</div>
            <div>{a.occupation ?? "—"}</div>
          </div>
          <div>
            <div className="muted">Visa subclass</div>
            <div>{a.visa_subclass ?? "—"}</div>
          </div>
        </div>
      </div>

      <h2>Assessments</h2>
      <DataTable
        columns={columns}
        rows={a.assessments}
        rowKey={(r) => r.assessment_id}
        emptyTitle="No assessments yet"
        emptyMessage="This applicant hasn't run an eligibility assessment yet."
      />
    </div>
  );
}
