import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../api/client.js";
import { useToast } from "../components/ToastContext.jsx";

const POLL_INTERVAL_MS = 1500;

// A 409 means one of two very different things: the sync lock is held, or the
// host behind this source refused a request and is paused (see
// FETCH_COOLDOWN_HOURS). The API tags the second with code "host_cooldown" so
// the two never collapse into the same misleading sentence — a busy server and
// a hostile one deserve different words.
function conflictMessage(err) {
  const detail = err?.body?.detail;
  if (detail && typeof detail === "object" && detail.code === "host_cooldown") {
    return detail.message;
  }
  return "A sync is already in progress — try again shortly.";
}

// Shared "Run now" / "Run all sources" behavior: polls GET /admin/runs/{id}
// until it settles, then shows a plain-language toast summary linking to
// Review. Pipeline-run internals (run ids, raw statuses) are never shown -
// only a non-blocking "sync in progress" state and the final summary.
export function useSyncRunner(onSettled) {
  const [running, setRunning] = useState(false);
  const { showToast } = useToast();
  const navigate = useNavigate();
  const intervalRef = useRef(null);

  const summarize = useCallback(
    async (job) => {
      if (job.status === "FAILED") {
        showToast(`Sync failed: ${job.error || "unknown error"}`);
        return;
      }
      const result = job.result || {};
      let reviewCount = null;
      try {
        const queue = await api.getReviewQueue({ status: "NEEDS_REVIEW" });
        reviewCount = queue.count;
      } catch {
        /* best-effort only - don't let this block the summary toast */
      }

      let message;
      if (typeof result.total_sources === "number") {
        message = `${result.changed} source${result.changed === 1 ? "" : "s"} updated, ${result.errors} failed`;
      } else if (result.source_id) {
        const plain = {
          changed: "content changed",
          unchanged: "unchanged",
          error: "failed",
          no_document: "no document found",
          cooldown: "paused — this host refused a request",
        };
        message = `${result.source_id}: ${plain[result.status] || result.status}`;
      } else {
        message = "Sync completed";
      }
      if (reviewCount !== null) {
        message += ` — ${reviewCount} requirement${reviewCount === 1 ? "" : "s"} need review`;
      }
      showToast(message, {
        duration: 12000,
        action: { label: "Open Review", onClick: () => navigate("/review") },
      });
    },
    [showToast, navigate]
  );

  const start = useCallback(
    (runId) => {
      setRunning(true);
      intervalRef.current = setInterval(async () => {
        try {
          const job = await api.getRun(runId);
          if (job.status === "COMPLETED" || job.status === "FAILED") {
            clearInterval(intervalRef.current);
            setRunning(false);
            await summarize(job);
            if (onSettled) onSettled();
          }
        } catch {
          clearInterval(intervalRef.current);
          setRunning(false);
        }
      }, POLL_INTERVAL_MS);
    },
    [summarize, onSettled]
  );

  const runOne = useCallback(
    async (sourceId) => {
      try {
        const result = await api.syncSource(sourceId);
        start(result.run_id);
      } catch (err) {
        if (err.status === 409) {
          showToast(conflictMessage(err));
        } else {
          showToast(err.message || "Could not start sync");
        }
      }
    },
    [start, showToast]
  );

  const runAll = useCallback(async () => {
    try {
      const result = await api.syncAllSources();
      start(result.run_id);
    } catch (err) {
      if (err.status === 409) {
        showToast(conflictMessage(err));
      } else {
        showToast(err.message || "Could not start sync");
      }
    }
  }, [start, showToast]);

  return { running, runOne, runAll };
}
