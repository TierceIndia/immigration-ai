import { useParams } from "react-router-dom";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { useSyncRunner } from "../hooks/useSyncRunner.js";
import StatusBadge from "../components/StatusBadge.jsx";
import DataTable from "../components/DataTable.jsx";
import DocumentCard from "../components/DocumentCard.jsx";
import ErrorState from "../components/ErrorState.jsx";
import EmptyState from "../components/EmptyState.jsx";
import SyncBanner from "../components/SyncBanner.jsx";

// The backend resolves each requirement's exact source via
// Requirement.document_version_id -> DocumentVersion.document_id ->
// GovernmentDocument.source_id (see admin_requirements.py::_req_to_review_item) -
// never guessed from program_id. Several real programs have more than one
// source (SKILLED_189 has 4), so fetching by program_id and keeping
// everything would silently mix in a sibling source's requirements. We fetch
// by program_id (there is no "list by source" endpoint) purely to get the
// candidate set, then partition client-side on the exact resolved source_id.
const REQUIREMENT_STATUSES = ["NEEDS_REVIEW", "APPROVED", "CONFLICT", "REJECTED"];

async function fetchRequirementsForProgram(programId) {
  const pages = await Promise.all(
    REQUIREMENT_STATUSES.map((status) => api.getReviewQueue({ status, programId }))
  );
  return pages.flatMap((p) => p.requirements);
}

function partitionBySource(requirements, sourceId) {
  const own = [];
  let unattributed = 0;
  for (const r of requirements) {
    if (r.source && r.source.source_id === sourceId) {
      own.push(r);
    } else if (!r.source) {
      // No document_version_id to resolve - never guessed into this (or
      // any) source's bucket, just counted honestly.
      unattributed += 1;
    }
    // Anything resolving to a DIFFERENT source_id belongs to a sibling
    // source sharing this program and is excluded entirely.
  }
  return { own, unattributed };
}

export default function SourceDetailPage() {
  const { sourceId } = useParams();
  const sourceState = useApi(() => api.getSource(sourceId), [sourceId]);
  const documentsState = useApi(() => api.listDocuments({ sourceId }), [sourceId]);
  const { running, elapsedSeconds, runOne } = useSyncRunner(() => {
    sourceState.reload();
    documentsState.reload();
  });

  const programId = sourceState.data?.program_id;
  const requirementsState = useApi(
    () => (programId ? fetchRequirementsForProgram(programId) : Promise.resolve([])),
    [programId]
  );

  if (sourceState.loading) return <p className="muted">Loading…</p>;
  if (sourceState.error) return <ErrorState error={sourceState.error} onRetry={sourceState.reload} />;

  const source = sourceState.data;
  const { own: ownRequirements, unattributed } = requirementsState.data
    ? partitionBySource(requirementsState.data, sourceId)
    : { own: null, unattributed: 0 };

  const reqColumns = [
    { key: "name", header: "Requirement", render: (r) => r.name },
    { key: "category", header: "Category" },
    { key: "field", header: "Field" },
    { key: "value", header: "Value", render: (r) => (r.value === null ? "—" : `${r.operator ?? ""} ${JSON.stringify(r.value)}`) },
    { key: "status", header: "Status", render: (r) => <StatusBadge value={r.status} /> },
  ];

  return (
    <div>
      <div className="flex-between">
        <h1>{source.source_id}</h1>
        <button className="btn btn-primary" onClick={() => runOne(source.source_id)} disabled={running}>
          Run this source
        </button>
      </div>

      {running && <SyncBanner elapsedSeconds={elapsedSeconds} />}

      <div className="card">
        <div className="card-grid">
          <div>
            <div className="muted">Authority</div>
            <div>{source.authority}</div>
          </div>
          <div>
            <div className="muted">Program</div>
            <div>{source.program_id} ({source.visa_category})</div>
          </div>
          <div>
            <div className="muted">Status</div>
            <StatusBadge value={source.status} />
          </div>
          <div>
            <div className="muted">Last result</div>
            <StatusBadge value={source.fetch_status} />
          </div>
        </div>
        <p>
          <a href={source.url} target="_blank" rel="noreferrer">
            {source.url}
          </a>
        </p>
        {source.last_error && (
          <p className="field-error">Last error: {source.last_error}</p>
        )}
      </div>

      <h2>Documents</h2>
      {documentsState.loading && <p className="muted">Loading documents…</p>}
      {documentsState.error && <ErrorState error={documentsState.error} onRetry={documentsState.reload} />}
      {documentsState.data && documentsState.data.length === 0 && (
        <EmptyState title="No documents yet" message="This source hasn't been fetched yet." />
      )}
      {documentsState.data?.map((doc) => (
        <DocumentCard key={doc.document_id} document={doc} />
      ))}

      <h2>Requirements extracted from this source</h2>
      {requirementsState.loading && <p className="muted">Loading requirements…</p>}
      {requirementsState.error && <ErrorState error={requirementsState.error} onRetry={requirementsState.reload} />}
      {ownRequirements && (
        <>
          <DataTable
            columns={reqColumns}
            rows={ownRequirements}
            rowKey={(r) => r.requirement_id}
            emptyTitle="No requirements yet"
            emptyMessage="Nothing has been extracted from this exact source yet."
          />
          {unattributed > 0 && (
            <p className="muted">
              {unattributed} requirement{unattributed === 1 ? "" : "s"} in this program{" "}
              {unattributed === 1 ? "has" : "have"} no document link and can't be attributed to
              this or any other source — not shown above.
            </p>
          )}
        </>
      )}
    </div>
  );
}
