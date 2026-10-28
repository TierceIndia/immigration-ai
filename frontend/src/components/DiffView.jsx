// Coarse chunk-level diff: a chunk whose exact text doesn't appear on the
// other side is highlighted as removed/added. Not a full LCS text diff, but
// honest at the granularity the backend actually gives us (whole chunks),
// and cheap enough to compute on every render.
export default function DiffView({ diff }) {
  if (!diff || diff.diff === null) {
    return <p className="muted">{diff?.message || "No diff available."}</p>;
  }

  const oldTexts = new Set(diff.old_chunks.map((c) => c.raw_text));
  const newTexts = new Set(diff.new_chunks.map((c) => c.raw_text));

  return (
    <div>
      <p className="muted">
        Comparing v{diff.from_version} → v{diff.to_version}
        {diff.change_type ? ` (${diff.change_type})` : ""}
      </p>
      {diff.diff_summary && <p>{diff.diff_summary}</p>}
      <div className="diff-grid">
        <div className="diff-pane">
          <h3>Before (v{diff.from_version})</h3>
          {diff.old_chunks.map((c) => (
            <p key={c.chunk_id} className={!newTexts.has(c.raw_text) ? "diff-removed" : undefined}>
              {c.raw_text}
            </p>
          ))}
        </div>
        <div className="diff-pane">
          <h3>After (v{diff.to_version})</h3>
          {diff.new_chunks.map((c) => (
            <p key={c.chunk_id} className={!oldTexts.has(c.raw_text) ? "diff-added" : undefined}>
              {c.raw_text}
            </p>
          ))}
        </div>
      </div>
    </div>
  );
}
