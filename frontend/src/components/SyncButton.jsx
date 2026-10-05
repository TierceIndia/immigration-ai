import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api.js";

const POLL_INTERVAL_MS = 1500;
const TERMINAL_STATUSES = new Set(["COMPLETED", "FAILED"]);

export default function SyncButton() {
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  function showMessage(text, ms = 4000) {
    setMessage(text);
    setTimeout(() => setMessage(null), ms);
  }

  function pollRun(runId) {
    pollRef.current = setInterval(async () => {
      try {
        const job = await api.getRun(runId);
        if (TERMINAL_STATUSES.has(job.status)) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setSyncing(false);
          if (job.status === "COMPLETED") {
            const summary = job.result
              ? `Sync complete — ${job.result.changed ?? 0} changed, ${
                  job.result.errors ?? 0
                } error(s)`
              : "Sync complete";
            showMessage(summary);
          } else {
            showMessage(`Sync failed: ${job.error || "unknown error"}`, 6000);
          }
          window.dispatchEvent(new CustomEvent("ik:sync-completed"));
        }
      } catch (err) {
        clearInterval(pollRef.current);
        pollRef.current = null;
        setSyncing(false);
        showMessage(`Lost track of sync progress: ${err.message}`, 6000);
      }
    }, POLL_INTERVAL_MS);
  }

  async function handleClick() {
    setSyncing(true);
    try {
      const result = await api.triggerSync();
      pollRun(result.run_id);
    } catch (err) {
      setSyncing(false);
      if (err instanceof ApiError && err.status === 409) {
        showMessage("A sync is already running — try again shortly.");
      } else {
        showMessage(`Could not start sync: ${err.message}`, 6000);
      }
    }
  }

  return (
    <>
      <button className="btn" onClick={handleClick} disabled={syncing}>
        {syncing && <span className="spinner" />}
        {syncing ? "Syncing…" : "Run Sync Now"}
      </button>
      {message && <div className="toast">{message}</div>}
    </>
  );
}
