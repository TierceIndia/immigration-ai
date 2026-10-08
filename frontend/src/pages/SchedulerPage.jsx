import { useEffect, useState } from "react";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { useToast } from "../components/ToastContext.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import ErrorState from "../components/ErrorState.jsx";

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function istPreview(hourUtc, minuteUtc) {
  if (hourUtc === "" || hourUtc === null || hourUtc === undefined) return "—";
  const totalMinutes = (Number(hourUtc) * 60 + Number(minuteUtc || 0) + 5 * 60 + 30) % (24 * 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")} IST`;
}

export default function SchedulerPage() {
  const schedulerState = useApi(() => api.getScheduler(), []);
  const { showToast } = useToast();
  const [form, setForm] = useState(null);
  const [errors, setErrors] = useState({});
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (schedulerState.data) setForm(schedulerState.data);
  }, [schedulerState.data]);

  if (schedulerState.loading || !form) return <p className="muted">Loading…</p>;
  if (schedulerState.error) return <ErrorState error={schedulerState.error} onRetry={schedulerState.reload} />;

  function set(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function validate() {
    const next = {};
    if (form.mode === "daily") {
      if (form.hour_utc === "" || form.hour_utc === null || form.hour_utc < 0 || form.hour_utc > 23) {
        next.hour_utc = "Hour must be 0-23";
      }
      if (form.minute_utc === "" || form.minute_utc === null || form.minute_utc < 0 || form.minute_utc > 59) {
        next.minute_utc = "Minute must be 0-59";
      }
    } else if (form.interval_minutes === "" || form.interval_minutes === null || form.interval_minutes < 15) {
      next.interval_minutes = "Interval must be at least 15 minutes";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function doSave() {
    try {
      const body = {
        enabled: form.enabled,
        mode: form.mode,
        hour_utc: form.mode === "daily" ? Number(form.hour_utc) : null,
        minute_utc: form.mode === "daily" ? Number(form.minute_utc) : null,
        interval_minutes: form.mode === "interval" ? Number(form.interval_minutes) : null,
      };
      const updated = await api.updateScheduler(body);
      setForm(updated);
      showToast("Schedule updated");
    } catch (err) {
      showToast(err.message || "Could not update schedule");
    } finally {
      setConfirming(false);
    }
  }

  function handleSave(e) {
    e.preventDefault();
    if (!validate()) return;
    setConfirming(true);
  }

  return (
    <div>
      <h1>Scheduler</h1>
      <div className="card">
        <h2>Current schedule</h2>
        <p>
          <span className="muted">Status: </span>
          {schedulerState.data.enabled ? "Enabled" : "Disabled"}
        </p>
        <p>
          <span className="muted">Mode: </span>
          {schedulerState.data.mode === "daily"
            ? `Daily at ${String(schedulerState.data.hour_utc).padStart(2, "0")}:${String(schedulerState.data.minute_utc).padStart(2, "0")} UTC`
            : `Every ${schedulerState.data.interval_minutes} minutes`}
        </p>
        <p>
          <span className="muted">Next run: </span>
          {schedulerState.data.enabled ? formatDateTime(schedulerState.data.next_run_at) : "—"}
        </p>
      </div>

      <form className="card" onSubmit={handleSave} noValidate>
        <h2>Change schedule</h2>
        <label className="flex" style={{ marginBottom: "var(--space-3)" }}>
          <input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} />
          Scheduler enabled
        </label>

        <div className="field">
          <label htmlFor="sched-mode">Mode</label>
          <select id="sched-mode" value={form.mode} onChange={(e) => set("mode", e.target.value)}>
            <option value="daily">Daily at a fixed time</option>
            <option value="interval">Every N minutes</option>
          </select>
        </div>

        {form.mode === "daily" ? (
          <div className="form-row">
            <div className="field">
              <label htmlFor="sched-hour">Hour (UTC)</label>
              <input
                id="sched-hour"
                type="number"
                min={0}
                max={23}
                className={errors.hour_utc ? "invalid" : ""}
                value={form.hour_utc ?? ""}
                onChange={(e) => set("hour_utc", e.target.value === "" ? "" : Number(e.target.value))}
              />
              {errors.hour_utc && <span className="field-error">{errors.hour_utc}</span>}
            </div>
            <div className="field">
              <label htmlFor="sched-minute">Minute</label>
              <input
                id="sched-minute"
                type="number"
                min={0}
                max={59}
                className={errors.minute_utc ? "invalid" : ""}
                value={form.minute_utc ?? ""}
                onChange={(e) => set("minute_utc", e.target.value === "" ? "" : Number(e.target.value))}
              />
              {errors.minute_utc && <span className="field-error">{errors.minute_utc}</span>}
            </div>
            <div className="field">
              <label>IST preview</label>
              <div style={{ padding: "8px 0" }}>{istPreview(form.hour_utc, form.minute_utc)}</div>
            </div>
          </div>
        ) : (
          <div className="field">
            <label htmlFor="sched-interval">Interval (minutes)</label>
            <input
              id="sched-interval"
              type="number"
              min={15}
              className={errors.interval_minutes ? "invalid" : ""}
              value={form.interval_minutes ?? ""}
              onChange={(e) => set("interval_minutes", e.target.value === "" ? "" : Number(e.target.value))}
            />
            {errors.interval_minutes && <span className="field-error">{errors.interval_minutes}</span>}
          </div>
        )}

        <button className="btn btn-primary" type="submit">
          Save schedule
        </button>
      </form>

      {confirming && (
        <ConfirmDialog
          title="Change the sync schedule?"
          message="This changes when the automated government source sync runs, effective immediately."
          confirmLabel="Save schedule"
          onConfirm={doSave}
          onCancel={() => setConfirming(false)}
        />
      )}
    </div>
  );
}
