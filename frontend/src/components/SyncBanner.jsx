// Non-blocking banner shown while a sync is in progress.
// Spec: "a non-blocking banner 'Sync in progress — this takes a few minutes
// by design' with elapsed time"
export default function SyncBanner({ elapsedSeconds }) {
  const mins = Math.floor(elapsedSeconds / 60);
  const secs = elapsedSeconds % 60;
  const elapsed =
    mins > 0
      ? `${mins}m ${String(secs).padStart(2, "0")}s`
      : `${secs}s`;

  return (
    <div className="sync-banner" role="status" aria-live="polite">
      <span className="sync-banner-dot" aria-hidden="true" />
      <span>
        Sync in progress — this takes a few minutes by design.{" "}
        <span className="sync-banner-elapsed">Elapsed: {elapsed}</span>
      </span>
    </div>
  );
}
