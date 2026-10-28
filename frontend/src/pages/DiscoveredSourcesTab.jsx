import { useState } from "react";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { useToast } from "../components/ToastContext.jsx";
import DataTable from "../components/DataTable.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import ErrorState from "../components/ErrorState.jsx";
import Modal from "../components/Modal.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { TableSkeleton } from "../components/Skeleton.jsx";

const STATUS_OPTIONS = ["", "PENDING_REVIEW", "APPROVED", "REJECTED"];
const VISA_CATEGORIES = ["SKILLED", "WORK", "STUDENT", "VISITOR"];
const SOURCE_ID_RE = /^SRC-[A-Z0-9][A-Z0-9_-]*$/;

function formatDateTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// Approve modal — admin must fill fields that the discovery crawler can't infer.
function ApproveModal({ candidate, onClose, onApproved }) {
  const { showToast } = useToast();
  const [form, setForm] = useState({
    source_id: "",
    program_id: "",
    visa_category: candidate.suggested_visa_category || "SKILLED",
    authority: "",
    priority: "PRIMARY",
    source_type: "HTML",
    crawl_frequency: "DAILY",
    notes: "",
  });
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);

  function set(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function validate() {
    const next = {};
    if (!SOURCE_ID_RE.test(form.source_id)) {
      next.source_id = "Must look like SRC-AU-189-MAIN";
    }
    if (!form.program_id.trim()) next.program_id = "Required";
    if (!form.authority.trim()) next.authority = "Required";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!validate()) return;
    setBusy(true);
    try {
      const body = {
        source_id: form.source_id,
        program_id: form.program_id,
        visa_category: form.visa_category,
        authority: form.authority,
        priority: form.priority,
        source_type: form.source_type,
        crawl_frequency: form.crawl_frequency,
        notes: form.notes || undefined,
      };
      await api.approveDiscoveredSource(candidate.discovered_source_id, body);
      showToast(`${form.source_id} approved and added to sources.`);
      onApproved();
    } catch (err) {
      showToast(err.message || "Could not approve candidate");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Approve discovered source" onClose={onClose}>
      <p className="muted" style={{ marginBottom: "var(--space-3)" }}>
        <strong>URL:</strong>{" "}
        <a href={candidate.candidate_url} target="_blank" rel="noreferrer">
          {candidate.candidate_url}
        </a>
      </p>
      <form onSubmit={handleSubmit} noValidate>
        <div className="field">
          <label htmlFor="ds-src-id">Source ID</label>
          <input
            id="ds-src-id"
            className={errors.source_id ? "invalid" : ""}
            value={form.source_id}
            onChange={(e) => set("source_id", e.target.value.toUpperCase())}
            placeholder="SRC-AU-189-MAIN"
          />
          {errors.source_id && <span className="field-error">{errors.source_id}</span>}
        </div>
        <div className="field">
          <label htmlFor="ds-program">Program ID</label>
          <input
            id="ds-program"
            className={errors.program_id ? "invalid" : ""}
            value={form.program_id}
            onChange={(e) => set("program_id", e.target.value.toUpperCase())}
            placeholder="SKILLED_189"
          />
          {errors.program_id && <span className="field-error">{errors.program_id}</span>}
        </div>
        <div className="form-row">
          <div className="field">
            <label htmlFor="ds-visa-cat">Visa category</label>
            <select
              id="ds-visa-cat"
              value={form.visa_category}
              onChange={(e) => set("visa_category", e.target.value)}
            >
              {VISA_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="ds-priority">Priority</label>
            <select
              id="ds-priority"
              value={form.priority}
              onChange={(e) => set("priority", e.target.value)}
            >
              <option value="PRIMARY">PRIMARY</option>
              <option value="SECONDARY">SECONDARY</option>
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="ds-authority">Authority</label>
          <input
            id="ds-authority"
            className={errors.authority ? "invalid" : ""}
            value={form.authority}
            onChange={(e) => set("authority", e.target.value)}
            placeholder="Home Affairs"
          />
          {errors.authority && <span className="field-error">{errors.authority}</span>}
        </div>
        <div className="form-row">
          <div className="field">
            <label htmlFor="ds-src-type">Source type</label>
            <select
              id="ds-src-type"
              value={form.source_type}
              onChange={(e) => set("source_type", e.target.value)}
            >
              <option value="HTML">HTML</option>
              <option value="PDF">PDF</option>
              <option value="API">API</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="ds-crawl-freq">Crawl frequency</label>
            <select
              id="ds-crawl-freq"
              value={form.crawl_frequency}
              onChange={(e) => set("crawl_frequency", e.target.value)}
            >
              <option value="DAILY">DAILY</option>
              <option value="WEEKLY">WEEKLY</option>
              <option value="MONTHLY">MONTHLY</option>
            </select>
          </div>
        </div>
        <div className="field">
          <label htmlFor="ds-notes">Notes (optional)</label>
          <textarea
            id="ds-notes"
            rows={2}
            value={form.notes}
            onChange={(e) => set("notes", e.target.value)}
          />
        </div>
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Approving…" : "Approve source"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export default function DiscoveredSourcesTab() {
  const [statusFilter, setStatusFilter] = useState("PENDING_REVIEW");
  const { showToast } = useToast();
  const discoveredState = useApi(
    () => api.listDiscoveredSources({ status: statusFilter || undefined }),
    [statusFilter]
  );

  const [approvingCandidate, setApprovingCandidate] = useState(null);
  const [rejectTarget, setRejectTarget] = useState(null);

  async function handleReject(candidate) {
    try {
      await api.rejectDiscoveredSource(
        candidate.discovered_source_id,
        "Rejected by admin"
      );
      showToast("Candidate rejected.");
      discoveredState.reload();
    } catch (err) {
      showToast(err.message || "Could not reject candidate");
    } finally {
      setRejectTarget(null);
    }
  }

  if (discoveredState.loading) {
    return <TableSkeleton rows={4} cols={5} />;
  }
  if (discoveredState.error) {
    return (
      <ErrorState error={discoveredState.error} onRetry={discoveredState.reload} />
    );
  }

  const columns = [
    {
      key: "candidate_url",
      header: "Candidate URL",
      render: (d) => (
        <a href={d.candidate_url} target="_blank" rel="noreferrer" style={{ wordBreak: "break-all" }}>
          {d.candidate_url}
        </a>
      ),
    },
    {
      key: "page_title",
      header: "Page title",
      render: (d) => d.page_title || <span className="muted">—</span>,
    },
    {
      key: "confidence_score",
      header: "Confidence",
      render: (d) => `${d.confidence_score}%`,
    },
    {
      key: "suggested_visa_category",
      header: "Suggested category",
      render: (d) => d.suggested_visa_category || <span className="muted">—</span>,
    },
    {
      key: "status",
      header: "Status",
      render: (d) => <StatusBadge value={d.status} />,
    },
    {
      key: "discovered_at",
      header: "Discovered",
      render: (d) => formatDateTime(d.discovered_at),
    },
    {
      key: "actions",
      header: "",
      render: (d) =>
        d.status === "PENDING_REVIEW" ? (
          <div className="flex">
            <button
              className="btn btn-sm btn-primary"
              onClick={() => setApprovingCandidate(d)}
            >
              Approve
            </button>
            <button
              className="btn btn-sm btn-danger"
              onClick={() => setRejectTarget(d)}
            >
              Reject
            </button>
          </div>
        ) : null,
    },
  ];

  return (
    <div>
      <div className="flex-between" style={{ marginBottom: "var(--space-3)" }}>
        <div className="field" style={{ marginBottom: 0, minWidth: 180 }}>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            aria-label="Filter by status"
          >
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s || "All"}
              </option>
            ))}
          </select>
        </div>
      </div>

      <DataTable
        columns={columns}
        rows={discoveredState.data}
        rowKey={(d) => d.discovered_source_id}
        emptyTitle="No discovered candidates"
        emptyMessage="Run source discovery from a seed URL to populate this list."
      />

      {approvingCandidate && (
        <ApproveModal
          candidate={approvingCandidate}
          onClose={() => setApprovingCandidate(null)}
          onApproved={() => {
            setApprovingCandidate(null);
            discoveredState.reload();
          }}
        />
      )}

      {rejectTarget && (
        <ConfirmDialog
          title="Reject this candidate?"
          message={`"${rejectTarget.page_title || rejectTarget.candidate_url}" will be marked as rejected and hidden from this list.`}
          confirmLabel="Reject"
          danger
          onConfirm={() => handleReject(rejectTarget)}
          onCancel={() => setRejectTarget(null)}
        />
      )}
    </div>
  );
}
