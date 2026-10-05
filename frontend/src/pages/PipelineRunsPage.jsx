import { useEffect, useState } from "react";
import { api } from "../api.js";
import StatusBadge from "../components/StatusBadge.jsx";

function formatTimestamp(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function changedLabel(run) {
  if (!run.old_hash && !run.new_hash) return "—";
  if (run.old_hash === run.new_hash) return "unchanged";
  return "changed";
}

export default function PipelineRunsPage() {
  const [runs, setRuns] = useState(null);
  const [error, setError] = useState(null);

  async function load() {
    try {
      const data = await api.listRuns(50);
      setRuns(data.runs || []);
      setError(null);
    } catch (err) {
      setError(err.message || "Failed to load pipeline runs");
    }
  }

  useEffect(() => {
    load();
    function onSyncCompleted() {
      load();
    }
    window.addEventListener("ik:sync-completed", onSyncCompleted);
    return () => window.removeEventListener("ik:sync-completed", onSyncCompleted);
  }, []);

  return (
    <div>
      <div className="page-title">
        <div>
          <h2>Pipeline Runs</h2>
          <p>Most recent government source sync runs, newest first.</p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {runs === null && !error && (
        <div className="loading-state">Loading pipeline runs…</div>
      )}

      {runs !== null && runs.length === 0 && (
        <div className="empty-state">No pipeline runs recorded yet.</div>
      )}

      {runs !== null && runs.length > 0 && (
        <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Run</th>
              <th>Source</th>
              <th>Trigger</th>
              <th>Status</th>
              <th>Change</th>
              <th>Started</th>
              <th>Requirements</th>
              <th>Rules created</th>
              <th>Errors</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id}>
                <td className="mono">{r.run_id.slice(0, 8)}</td>
                <td>{r.source_id || "—"}</td>
                <td className="muted">{r.trigger_type}</td>
                <td>
                  <StatusBadge value={r.status} />
                </td>
                <td className="muted">{changedLabel(r)}</td>
                <td>{formatTimestamp(r.started_at)}</td>
                <td>{r.requirements_extracted ?? 0}</td>
                <td>{r.rules_created ?? 0}</td>
                <td>
                  {r.errors && r.errors.length > 0 ? (
                    <span
                      className="badge badge-error"
                      title={r.errors.join("\n")}
                    >
                      {r.errors.length}
                    </span>
                  ) : (
                    <span className="muted">0</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
