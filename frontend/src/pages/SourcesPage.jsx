import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { useSyncRunner } from "../hooks/useSyncRunner.js";
import DataTable from "../components/DataTable.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import ErrorState from "../components/ErrorState.jsx";
import { TableSkeleton } from "../components/Skeleton.jsx";
import AddSourceForm from "./AddSourceForm.jsx";
import { useToast } from "../components/ToastContext.jsx";

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// True while the source's HOST is in a refusal cooldown: it answered one of
// our requests with 403/429, so sync skips every source on it until then.
// Evaluated at render time so an already-expired pause never disables a button.
function isPaused(source, now = Date.now()) {
  if (!source.blocked_until) return false;
  const until = Date.parse(source.blocked_until);
  return Number.isFinite(until) && until > now;
}

export default function SourcesPage() {
  const sourcesState = useApi(() => api.listSources(), []);
  const { running, runOne, runAll } = useSyncRunner(() => sourcesState.reload());
  const [showAddForm, setShowAddForm] = useState(false);
  const { showToast } = useToast();

  const knownPrograms = useMemo(() => {
    if (!sourcesState.data) return [];
    const seen = new Map();
    for (const s of sourcesState.data) {
      if (!seen.has(s.program_id)) {
        seen.set(s.program_id, { program_id: s.program_id, country_id: s.country_id, visa_category: s.visa_category });
      }
    }
    return Array.from(seen.values());
  }, [sourcesState.data]);

  async function toggleEnabled(source) {
    const nextStatus = source.status === "DISABLED" ? "ACTIVE" : "DISABLED";
    try {
      await api.updateSource(source.source_id, { status: nextStatus });
      sourcesState.reload();
    } catch (err) {
      showToast(err.message || "Could not update source");
    }
  }

  if (sourcesState.loading) {
    return (
      <div>
        <h1>Sources</h1>
        <TableSkeleton rows={6} cols={9} />
      </div>
    );
  }
  if (sourcesState.error) {
    return <ErrorState error={sourcesState.error} onRetry={sourcesState.reload} />;
  }

  const columns = [
    {
      key: "source_id",
      header: "Source",
      render: (s) => (
        <Link to={`/sources/${encodeURIComponent(s.source_id)}`}>{s.source_id}</Link>
      ),
    },
    { key: "program_id", header: "Program", render: (s) => `${s.program_id} (${s.visa_category})` },
    {
      key: "url",
      header: "URL",
      render: (s) => (
        <a href={s.url} target="_blank" rel="noreferrer" style={{ wordBreak: "break-all" }}>
          {s.url}
        </a>
      ),
    },
    { key: "status", header: "Status", render: (s) => <StatusBadge value={s.status} /> },
    { key: "last_checked_at", header: "Last checked", render: (s) => formatDateTime(s.last_checked_at) },
    { key: "fetch_status", header: "Last result", render: (s) => <StatusBadge value={s.fetch_status} /> },
    { key: "consecutive_failures", header: "Failures", render: (s) => s.consecutive_failures ?? 0 },
    {
      key: "blocked_until",
      header: "Paused until",
      render: (s) => (isPaused(s) ? formatDateTime(s.blocked_until) : "—"),
    },
    {
      key: "actions",
      header: "",
      render: (s) => {
        const paused = isPaused(s);
        return (
          <div className="flex">
            <button
              className="btn btn-sm"
              onClick={() => runOne(s.source_id)}
              disabled={running || paused}
              title={
                paused
                  ? `This host refused our last request. Sync resumes after ${formatDateTime(s.blocked_until)}.`
                  : undefined
              }
            >
              {paused ? "Paused" : "Run now"}
            </button>
            <button className="btn btn-sm" onClick={() => toggleEnabled(s)} disabled={running}>
              {s.status === "DISABLED" ? "Enable" : "Disable"}
            </button>
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <div className="flex-between">
        <h1>Sources</h1>
        <div className="flex">
          <button className="btn" onClick={runAll} disabled={running}>
            {running ? "Sync in progress…" : "Run all sources"}
          </button>
          <button className="btn btn-primary" onClick={() => setShowAddForm(true)}>
            Add source
          </button>
        </div>
      </div>

      <DataTable
        columns={columns}
        rows={sourcesState.data}
        rowKey={(s) => s.source_id}
        emptyTitle="No sources yet"
        emptyMessage="Add your first government source to start tracking it."
      />

      {showAddForm && (
        <AddSourceForm
          knownPrograms={knownPrograms}
          onClose={() => setShowAddForm(false)}
          onCreated={() => {
            setShowAddForm(false);
            sourcesState.reload();
          }}
        />
      )}
    </div>
  );
}
