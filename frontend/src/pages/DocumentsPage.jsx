import { useState } from "react";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import DocumentCard from "../components/DocumentCard.jsx";
import ErrorState from "../components/ErrorState.jsx";
import EmptyState from "../components/EmptyState.jsx";

const STATUS_OPTIONS = ["", "CURRENT", "SUPERSEDED", "REMOVED"];

export default function DocumentsPage() {
  const [sourceId, setSourceId] = useState("");
  const [status, setStatus] = useState("");

  const documentsState = useApi(
    () => api.listDocuments({ sourceId: sourceId || undefined, status: status || undefined }),
    [sourceId, status]
  );

  return (
    <div>
      <h1>Documents</h1>
      <div className="form-row">
        <div className="field">
          <label htmlFor="docs-source">Source ID</label>
          <input
            id="docs-source"
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value.toUpperCase())}
            placeholder="e.g. SRC-AU-189-MAIN"
          />
        </div>
        <div className="field">
          <label htmlFor="docs-status">Status</label>
          <select id="docs-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s || "All"}
              </option>
            ))}
          </select>
        </div>
      </div>

      {documentsState.loading && <p className="muted">Loading documents…</p>}
      {documentsState.error && <ErrorState error={documentsState.error} onRetry={documentsState.reload} />}
      {documentsState.data && documentsState.data.length === 0 && (
        <EmptyState title="No documents found" message="Try a different filter." />
      )}
      {documentsState.data?.map((doc) => (
        <DocumentCard key={doc.document_id} document={doc} />
      ))}
    </div>
  );
}
