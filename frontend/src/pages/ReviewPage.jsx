import { useMemo, useState } from "react";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { useToast } from "../components/ToastContext.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import ErrorState from "../components/ErrorState.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { TableSkeleton } from "../components/Skeleton.jsx";

const STATUS_OPTIONS = ["NEEDS_REVIEW", "CONFLICT", "APPROVED", "REJECTED"];

function groupBySource(requirements) {
  const groups = new Map();
  for (const r of requirements) {
    const key = r.source ? `${r.program_id ?? ""}·${r.source.source_id}` : r.category;
    const label = r.source ? `${r.source.source_id} — ${r.source.authority ?? ""}` : "Unknown source";
    if (!groups.has(key)) groups.set(key, { label, items: [] });
    groups.get(key).items.push(r);
  }
  return Array.from(groups.values());
}

function ValueLine({ item }) {
  return (
    <span>
      <code>{item.field}</code> {item.operator} {JSON.stringify(item.value)}
      {item.unit ? ` ${item.unit}` : ""}
    </span>
  );
}

function RequirementItem({ item, selected, onToggleSelect, onAction }) {
  const [busy, setBusy] = useState(false);

  async function act(action) {
    setBusy(true);
    try {
      await onAction(item.requirement_id, action);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card-header">
        <label className="flex">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelect(item.requirement_id)}
            aria-label={`Select ${item.name}`}
          />
          <strong>{item.name}</strong>
          <StatusBadge value={item.status} />
          {item.mandatory && <span className="badge badge-neutral">Mandatory</span>}
          {item.evaluable ? (
            <span className="badge badge-ok" title="This requirement's field maps to a real applicant fact and can resolve to pass/fail.">
              Evaluable
            </span>
          ) : (
            <span
              className="badge badge-warn"
              title="This requirement's underlying concept isn't wired into applicant data yet, so it can never resolve to pass/fail - only Unknown."
            >
              Not modeled{item.concept ? `: ${item.concept}` : ""}
            </span>
          )}
        </label>
        <span
          className="muted"
          title="Model-reported, low signal - the extraction model's own confidence estimate, not a measure of whether this requirement is correct or evaluable."
        >
          {item.confidence !== null && item.confidence !== undefined
            ? `${Math.round(item.confidence * 100)}% confidence (model-reported, low signal)`
            : ""}
        </span>
      </div>

      {item.description && <p>{item.description}</p>}
      <p>
        <ValueLine item={item} />
      </p>

      {item.evidence?.length > 0 && (
        <div>
          <h3>Evidence</h3>
          {item.evidence.map((e, i) => (
            <p key={i} className="muted">“{e.evidence_text}”</p>
          ))}
        </div>
      )}

      {item.source && (
        <p>
          <a href={item.source.url} target="_blank" rel="noreferrer">
            View source document
          </a>
          {item.document_version != null && <span className="muted"> (version {item.document_version})</span>}
        </p>
      )}

      {item.previous_value && (
        <div className="diff-grid">
          <div className="diff-pane">
            <h3>Before</h3>
            <p className="diff-removed">
              {item.previous_value.operator} {JSON.stringify(item.previous_value.value)}
            </p>
          </div>
          <div className="diff-pane">
            <h3>After</h3>
            <p className="diff-added">
              {item.operator} {JSON.stringify(item.value)}
            </p>
          </div>
        </div>
      )}

      <div className="modal-actions" style={{ marginTop: "var(--space-3)", justifyContent: "flex-start" }}>
        <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => act("approve")}>
          Approve
        </button>
        <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => act("reject")}>
          Reject
        </button>
        <button className="btn btn-sm" disabled={busy} onClick={() => act("needs_review")}>
          Needs review
        </button>
      </div>
    </div>
  );
}

export default function ReviewPage() {
  const [status, setStatus] = useState("NEEDS_REVIEW");
  // Item 4: on by default - a requirement whose field is NOT_MODELED (or
  // blank) can never resolve to pass/fail, so reviewer time defaults to the
  // requirements a decision can actually act on. The toggle still reaches
  // the hidden ones; nothing is ever deleted.
  const [evaluableOnly, setEvaluableOnly] = useState(true);
  const [selected, setSelected] = useState(() => new Set());
  const [confirmAction, setConfirmAction] = useState(null);
  const { showToast } = useToast();

  const queueState = useApi(
    () => api.getReviewQueue({ status, evaluableOnly }),
    [status, evaluableOnly]
  );

  const groups = useMemo(
    () => (queueState.data ? groupBySource(queueState.data.requirements) : []),
    [queueState.data]
  );

  function toggleSelect(id) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function applyAction(requirementId, action) {
    try {
      if (action === "approve") await api.approveRequirement(requirementId);
      else if (action === "reject") await api.rejectRequirement(requirementId);
      else await api.markNeedsReviewRequirement(requirementId);
      queueState.reload();
    } catch (err) {
      showToast(err.message || "Action failed");
    }
  }

  async function applyBulkAction(action) {
    const ids = Array.from(selected);
    for (const id of ids) {
      // eslint-disable-next-line no-await-in-loop
      await applyAction(id, action);
    }
    setSelected(new Set());
    setConfirmAction(null);
  }

  if (queueState.loading) {
    return (
      <div>
        <h1>Review</h1>
        <TableSkeleton rows={4} cols={3} />
      </div>
    );
  }
  if (queueState.error) {
    return <ErrorState error={queueState.error} onRetry={queueState.reload} />;
  }

  return (
    <div>
      <div className="flex-between">
        <h1>Review</h1>
        <div className="flex" style={{ gap: "var(--space-3)" }}>
          <label
            className="flex"
            title="Hide requirements whose field can never resolve to pass/fail (NOT_MODELED, or no field). Nothing is deleted - this only filters the view."
          >
            <input
              type="checkbox"
              checked={evaluableOnly}
              onChange={(e) => setEvaluableOnly(e.target.checked)}
            />
            Evaluable only
          </label>
          <div className="field" style={{ marginBottom: 0, minWidth: 180 }}>
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {selected.size > 0 && (
        <div className="card flex-between">
          <span>{selected.size} selected</span>
          <div className="flex">
            <button className="btn btn-sm btn-primary" onClick={() => setConfirmAction("approve")}>
              Approve selected
            </button>
            <button className="btn btn-sm btn-danger" onClick={() => setConfirmAction("reject")}>
              Reject selected
            </button>
            <button className="btn btn-sm" onClick={() => setConfirmAction("needs_review")}>
              Mark needs review
            </button>
          </div>
        </div>
      )}

      {groups.length === 0 && (
        <EmptyState title="Queue is clear" message={`No requirements with status ${status.replace(/_/g, " ")}.`} />
      )}

      {groups.map((group) => (
        <div key={group.label}>
          <h2>{group.label}</h2>
          {group.items.map((item) => (
            <RequirementItem
              key={item.requirement_id}
              item={item}
              selected={selected.has(item.requirement_id)}
              onToggleSelect={toggleSelect}
              onAction={applyAction}
            />
          ))}
        </div>
      ))}

      {confirmAction && (
        <ConfirmDialog
          title={`${confirmAction === "approve" ? "Approve" : confirmAction === "reject" ? "Reject" : "Mark needs review"} ${selected.size} requirement${selected.size === 1 ? "" : "s"}?`}
          message={`This will apply to ${selected.size} selected requirement${selected.size === 1 ? "" : "s"}.`}
          confirmLabel="Confirm"
          danger={confirmAction === "reject"}
          onConfirm={() => applyBulkAction(confirmAction)}
          onCancel={() => setConfirmAction(null)}
        />
      )}
    </div>
  );
}
