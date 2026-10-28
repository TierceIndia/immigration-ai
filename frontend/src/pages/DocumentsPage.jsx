import { useState } from "react";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import DocumentCard from "../components/DocumentCard.jsx";
import ErrorState from "../components/ErrorState.jsx";
import EmptyState from "../components/EmptyState.jsx";

const STATUS_OPTIONS = ["", "CURRENT", "SUPERSEDED", "REMOVED"];
const MODE_OPTIONS = [
  ["auto", "By meaning where possible"],
  ["keyword", "By keyword"],
  ["semantic", "By meaning only"],
];

export default function DocumentsPage() {
  const [sourceId, setSourceId] = useState("");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [mode, setMode] = useState("auto");

  const documentsState = useApi(
    () => api.listDocuments({ sourceId: sourceId || undefined, status: status || undefined }),
    [sourceId, status]
  );

  // Only runs once the admin submits a query; searching and browsing are two
  // different views of the same stored text.
  const searchState = useApi(
    () =>
      submitted
        ? api.searchDocuments({ query: submitted, mode, limit: 20 })
        : Promise.resolve(null),
    [submitted, mode]
  );

  function runSearch(e) {
    e.preventDefault();
    if (!query.trim()) return;
    setSubmitted(query.trim());
  }

  function clearSearch() {
    setQuery("");
    setSubmitted("");
  }

  return (
    <div>
      <h1>Documents</h1>

      <form className="form-row" onSubmit={runSearch}>
        <div className="field" style={{ flex: "3 1 320px" }}>
          <label htmlFor="docs-search">Search the stored text</label>
          <input
            id="docs-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. English language requirement"
            autoComplete="off"
          />
        </div>
        <div className="field">
          <label htmlFor="docs-mode">How to match</label>
          <select id="docs-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
            {MODE_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ alignSelf: "flex-end", flex: "0 0 auto" }}>
          <div className="flex">
            <button className="btn btn-primary" type="submit" disabled={!query.trim()}>
              Search
            </button>
            {submitted && (
              <button className="btn" type="button" onClick={clearSearch}>
                Clear
              </button>
            )}
          </div>
        </div>
      </form>

      {submitted ? (
        <SearchResults state={searchState} query={submitted} onClear={clearSearch} />
      ) : (
        <>
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
        </>
      )}
    </div>
  );
}

function SearchResults({ state, query, onClear }) {
  if (state.loading) return <p className="muted">Searching…</p>;

  if (state.error) {
    // A 409 is not a transient failure: semantic search was asked for and is
    // not available. Say why instead of offering a pointless retry.
    return (
      <div className="card" role="alert">
        <h2>Search unavailable</h2>
        <p>{state.error.message}</p>
        <button className="btn btn-sm" onClick={onClear}>
          Try a keyword search
        </button>
      </div>
    );
  }

  const data = state.data;
  if (!data) return null;

  return (
    <section aria-label="Search results">
      <div className="card">
        <div className="flex-between">
          <h2>
            {data.total} result{data.total === 1 ? "" : "s"} for “{query}”
          </h2>
          <span className="badge badge-neutral">
            {data.mode === "semantic" ? "Matched by meaning" : "Matched by keyword"}
          </span>
        </div>
        {data.mode === "keyword" && !data.semantic_available && (
          <p className="muted">
            Meaning-based search is off or has nothing embedded yet, so this matched words
            directly. It never silently pretends to understand the question.
          </p>
        )}
      </div>

      {data.total === 0 ? (
        <EmptyState
          title="Nothing matched"
          message="Try different words, or switch how the search matches."
        />
      ) : (
        data.results.map((hit) => (
          <article className="card" key={hit.chunk_id}>
            <div className="flex-between">
              <strong>{hit.title || hit.document_id}</strong>
              <span className="muted">{hit.citation || hit.document_id}</span>
            </div>
            {hit.heading_path && <p className="muted">{hit.heading_path}</p>}
            <p>{hit.snippet}</p>
            {hit.similarity != null && (
              <p className="muted">Similarity {Math.round(hit.similarity * 100)}%</p>
            )}
          </article>
        ))
      )}

    </section>
  );
}
