import { Link } from "react-router-dom";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import ErrorState from "../components/ErrorState.jsx";
import { LineSkeleton } from "../components/Skeleton.jsx";

const BLOCKED_FETCH_STATUSES = new Set([
  "ACCESS_BLOCKED",
  "NETWORK_RESTRICTED",
  "REQUIRES_BROWSER",
  "REQUIRES_MANUAL_DOWNLOAD",
]);

function summarizeSources(sources) {
  let healthy = 0;
  let failing = 0;
  let lastChecked = null;
  for (const s of sources) {
    const isFailing =
      s.status === "BROKEN" ||
      s.status === "NEEDS_MANUAL_CAPTURE" ||
      BLOCKED_FETCH_STATUSES.has(s.fetch_status) ||
      (s.consecutive_failures || 0) > 0;
    if (isFailing) failing += 1;
    else if (s.status === "ACTIVE") healthy += 1;
    if (s.last_checked_at && (!lastChecked || s.last_checked_at > lastChecked)) {
      lastChecked = s.last_checked_at;
    }
  }
  return { total: sources.length, healthy, failing, lastChecked };
}

function StatTile({ label, value, to }) {
  const body = (
    <div className="stat-tile">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
  return to ? <Link to={to} style={{ textDecoration: "none", color: "inherit" }}>{body}</Link> : body;
}

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function DashboardPage() {
  const sourcesState = useApi(() => api.listSources(), []);
  const reviewState = useApi(() => api.getReviewQueue({ status: "NEEDS_REVIEW" }), []);
  const rulesQueueState = useApi(() => api.getRulesQueue(), []);
  const schedulerState = useApi(() => api.getScheduler(), []);

  const anyLoading =
    sourcesState.loading || reviewState.loading || rulesQueueState.loading || schedulerState.loading;
  const firstError = sourcesState.error || reviewState.error || rulesQueueState.error || schedulerState.error;

  if (firstError) {
    return (
      <ErrorState
        error={firstError}
        onRetry={() => {
          sourcesState.reload();
          reviewState.reload();
          rulesQueueState.reload();
          schedulerState.reload();
        }}
      />
    );
  }

  const summary = sourcesState.data ? summarizeSources(sourcesState.data) : null;

  return (
    <div>
      <h1>Dashboard</h1>
      <div className="card-grid">
        <StatTile label="Total sources" value={anyLoading ? <LineSkeleton width="40px" /> : summary.total} to="/sources" />
        <StatTile label="Healthy sources" value={anyLoading ? <LineSkeleton width="40px" /> : summary.healthy} to="/sources" />
        <StatTile label="Failing sources" value={anyLoading ? <LineSkeleton width="40px" /> : summary.failing} to="/sources" />
        <StatTile
          label="Requirements awaiting review"
          value={anyLoading ? <LineSkeleton width="40px" /> : reviewState.data.count}
          to="/review"
        />
        <StatTile
          label="Draft rules"
          value={anyLoading ? <LineSkeleton width="40px" /> : rulesQueueState.data.counts.drafts}
          to="/rules"
        />
        <StatTile
          label="Rules awaiting approval"
          value={anyLoading ? <LineSkeleton width="40px" /> : rulesQueueState.data.counts.validated}
          to="/rules"
        />
      </div>

      <div className="card">
        <h2>Sync status</h2>
        <p>
          <span className="muted">Last checked source: </span>
          {anyLoading ? <LineSkeleton width="160px" /> : formatDateTime(summary.lastChecked)}
        </p>
        <p>
          <span className="muted">Next scheduled run: </span>
          {anyLoading ? (
            <LineSkeleton width="160px" />
          ) : schedulerState.data.enabled ? (
            formatDateTime(schedulerState.data.next_run_at)
          ) : (
            "Scheduler disabled"
          )}
        </p>
      </div>
    </div>
  );
}
