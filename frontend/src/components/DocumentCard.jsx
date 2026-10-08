import { useState } from "react";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import StatusBadge from "./StatusBadge.jsx";
import DiffView from "./DiffView.jsx";

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function DocumentCard({ document }) {
  const [expanded, setExpanded] = useState(false);
  const [showChunks, setShowChunks] = useState(false);
  const [diffFrom, setDiffFrom] = useState("");
  const [diffTo, setDiffTo] = useState("");

  const versionsState = useApi(
    () => (expanded ? api.getDocumentVersions(document.document_id) : Promise.resolve(null)),
    [expanded, document.document_id]
  );
  const chunksState = useApi(
    () => (showChunks ? api.getDocumentChunks(document.document_id) : Promise.resolve(null)),
    [showChunks, document.document_id]
  );
  const diffState = useApi(
    () =>
      expanded && versionsState.data && versionsState.data.length > 1
        ? api.getDocumentDiff(
            document.document_id,
            diffFrom || undefined,
            diffTo || undefined
          )
        : Promise.resolve(null),
    [expanded, versionsState.data, diffFrom, diffTo, document.document_id]
  );

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <strong>{document.title || document.document_id}</strong> <StatusBadge value={document.status} />
        </div>
        <button className="btn btn-sm" onClick={() => setExpanded((e) => !e)}>
          {expanded ? "Hide details" : "View details"}
        </button>
      </div>
      <p className="muted">
        v{document.current_version} · retrieved {formatDateTime(document.retrieved_at)}
      </p>

      {expanded && (
        <>
          {versionsState.loading && <p className="muted">Loading versions…</p>}
          {versionsState.error && <p className="field-error">Could not load versions.</p>}
          {versionsState.data && (
            <>
              <h3>Versions</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Version</th>
                      <th>Retrieved</th>
                      <th>Change type</th>
                      <th>Hash</th>
                    </tr>
                  </thead>
                  <tbody>
                    {versionsState.data.map((v) => (
                      <tr key={v.version_id}>
                        <td>{v.version_number}</td>
                        <td>{formatDateTime(v.retrieved_at)}</td>
                        <td>
                          <StatusBadge value={v.change_type} />
                        </td>
                        <td className="faint">{v.content_hash?.slice(0, 10)}…</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {versionsState.data.length > 1 && (
                <>
                  <h3>What changed</h3>
                  <div className="form-row">
                    <div className="field">
                      <label htmlFor={`diff-from-${document.document_id}`}>From version</label>
                      <select
                        id={`diff-from-${document.document_id}`}
                        value={diffFrom}
                        onChange={(e) => setDiffFrom(e.target.value)}
                      >
                        <option value="">(previous)</option>
                        {versionsState.data.map((v) => (
                          <option key={v.version_id} value={v.version_number}>
                            v{v.version_number}
                          </option>
                        ))}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor={`diff-to-${document.document_id}`}>To version</label>
                      <select
                        id={`diff-to-${document.document_id}`}
                        value={diffTo}
                        onChange={(e) => setDiffTo(e.target.value)}
                      >
                        <option value="">(latest)</option>
                        {versionsState.data.map((v) => (
                          <option key={v.version_id} value={v.version_number}>
                            v{v.version_number}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                  {diffState.loading && <p className="muted">Loading diff…</p>}
                  {diffState.data && <DiffView diff={diffState.data} />}
                </>
              )}

              <button className="btn btn-sm" onClick={() => setShowChunks((v) => !v)}>
                {showChunks ? "Hide content" : "View content"}
              </button>
              {chunksState.loading && <p className="muted">Loading content…</p>}
              {chunksState.data && (
                <div className="card">
                  {chunksState.data.length === 0 && <p className="muted">No chunks for this version.</p>}
                  {chunksState.data.map((c) => (
                    <p key={c.chunk_id}>
                      {c.heading_path && <strong>{c.heading_path}: </strong>}
                      {c.raw_text}
                    </p>
                  ))}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
