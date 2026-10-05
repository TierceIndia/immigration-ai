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

export default function SourcesDashboard() {
  const [sources, setSources] = useState(null);
  const [error, setError] = useState(null);

  async function load() {
    try {
      const data = await api.listSources();
      setSources(data);
      setError(null);
    } catch (err) {
      setError(err.message || "Failed to load sources");
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
          <h2>Government Sources</h2>
          <p>Real-time fetch status for every registered government source.</p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {sources === null && !error && (
        <div className="loading-state">Loading sources…</div>
      )}

      {sources !== null && sources.length === 0 && (
        <div className="empty-state">No sources registered yet.</div>
      )}

      {sources !== null && sources.length > 0 && (
        <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Source</th>
              <th>Program</th>
              <th>Status</th>
              <th>Fetch status</th>
              <th>Last checked</th>
              <th>Consecutive failures</th>
              <th>Last error</th>
            </tr>
          </thead>
          <tbody>
            {sources.map((s) => (
              <tr key={s.source_id}>
                <td>
                  <div>{s.source_id}</div>
                  <div className="muted mono">{s.url}</div>
                </td>
                <td>
                  {s.program_id}
                  <div className="muted">{s.visa_category}</div>
                </td>
                <td>
                  <StatusBadge value={s.status} />
                </td>
                <td>
                  <StatusBadge value={s.fetch_status} />
                </td>
                <td>{formatTimestamp(s.last_checked_at)}</td>
                <td>
                  {s.consecutive_failures != null && s.consecutive_failures > 0 ? (
                    <span className="badge badge-warn">{s.consecutive_failures}</span>
                  ) : (
                    <span className="muted">0</span>
                  )}
                </td>
                <td className="muted" style={{ whiteSpace: "normal", maxWidth: 280 }}>
                  {s.last_error || "—"}
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
