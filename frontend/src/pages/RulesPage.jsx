import { useState } from "react";
import * as api from "../api/client.js";
import { useApi } from "../hooks/useApi.js";
import { useToast } from "../components/ToastContext.jsx";
import StatusBadge from "../components/StatusBadge.jsx";
import DataTable from "../components/DataTable.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import ErrorState from "../components/ErrorState.jsx";
import RuleTestPanel from "../components/RuleTestPanel.jsx";
import { TableSkeleton } from "../components/Skeleton.jsx";

const STATUS_OPTIONS = ["", "DRAFT", "VALIDATED", "APPROVED", "ACTIVE", "REJECTED", "SUPERSEDED", "RETIRED"];

function readableExpression(rule) {
  const parts = [rule.field, rule.operator, JSON.stringify(rule.value)];
  if (rule.unit) parts.push(rule.unit);
  return parts.filter(Boolean).join(" ");
}

// Confirm dialogs are required specifically for activate/retire (they change
// live assessments). Approvals stay two explicit human steps - a rule is
// never auto-activated just because its requirement was approved.
const NEEDS_CONFIRM = new Set(["activate", "retire"]);

const NEXT_ACTION = {
  DRAFT: { action: "validate", label: "Validate" },
  VALIDATED: { action: "approve", label: "Approve" },
  APPROVED: { action: "activate", label: "Activate" },
  ACTIVE: { action: "retire", label: "Retire" },
};

const ACTION_FN = {
  validate: api.validateRule,
  approve: api.approveRule,
  reject: api.rejectRule,
  activate: api.activateRule,
  retire: api.retireRule,
};

export default function RulesPage() {
  const [status, setStatus] = useState("");
  const [programId, setProgramId] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [pendingConfirm, setPendingConfirm] = useState(null); // { action, ruleIds, label }
  const [testTarget, setTestTarget] = useState(null); // { programId, ruleIds }
  const { showToast } = useToast();

  const rulesState = useApi(() => api.listRules({ status: status || undefined, programId: programId || undefined }), [
    status,
    programId,
  ]);

  function toggleSelect(ruleId) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(ruleId)) next.delete(ruleId);
      else next.add(ruleId);
      return next;
    });
  }

  // A rule is only evaluable if every field it checks is one an applicant
  // can actually supply. The API refuses to activate the others without a
  // typed confirmation; the dialog below asks for the same word.
  function notEvaluableIn(ruleIds) {
    const byId = new Map((rulesState.data || []).map((r) => [r.rule_id, r]));
    return ruleIds.map((id) => byId.get(id)).filter((r) => r && r.evaluable === false);
  }

  async function runAction(action, ruleIds, { confirmNotEvaluable } = {}) {
    try {
      for (const id of ruleIds) {
        // eslint-disable-next-line no-await-in-loop
        await ACTION_FN[action](id, { confirmNotEvaluable });
      }
      setSelected(new Set());
      rulesState.reload();
    } catch (err) {
      showToast(err.message || "Action failed");
    }
  }

  function requestAction(action, ruleIds, label) {
    if (NEEDS_CONFIRM.has(action)) {
      const blocked = action === "activate" ? notEvaluableIn(ruleIds) : [];
      setPendingConfirm({
        action,
        ruleIds,
        label,
        notEvaluable: blocked,
        requireText: blocked.length > 0 ? "ACTIVATE" : undefined,
      });
    } else {
      runAction(action, ruleIds);
    }
  }

  async function runBatch(action, { confirmNotEvaluable } = {}) {
    const ids = Array.from(selected);
    try {
      if (action === "validate") await api.batchValidateRules(ids);
      else if (action === "approve") await api.batchApproveRules(ids);
      else if (action === "activate") await api.batchActivateRules(ids, { confirmNotEvaluable });
      setSelected(new Set());
      rulesState.reload();
    } catch (err) {
      showToast(err.message || "Batch action failed");
    }
  }

  function requestBatch(action) {
    if (NEEDS_CONFIRM.has(action)) {
      const ruleIds = Array.from(selected);
      const blocked = action === "activate" ? notEvaluableIn(ruleIds) : [];
      setPendingConfirm({
        action,
        batch: true,
        ruleIds,
        notEvaluable: blocked,
        requireText: blocked.length > 0 ? "ACTIVATE" : undefined,
      });
    } else {
      runBatch(action);
    }
  }

  if (rulesState.loading) {
    return (
      <div>
        <h1>Rules</h1>
        <TableSkeleton rows={6} cols={6} />
      </div>
    );
  }
  if (rulesState.error) {
    return <ErrorState error={rulesState.error} onRetry={rulesState.reload} />;
  }

  const columns = [
    {
      key: "select",
      header: "",
      render: (r) => (
        <input
          type="checkbox"
          checked={selected.has(r.rule_id)}
          onChange={() => toggleSelect(r.rule_id)}
          aria-label={`Select ${r.rule_id}`}
        />
      ),
    },
    { key: "program_id", header: "Program" },
    { key: "category", header: "Category" },
    { key: "expression", header: "Rule", render: (r) => <code>{readableExpression(r)}</code> },
    {
      key: "flags",
      header: "Flags",
      render: (r) => (
        <>
          {r.mandatory && <span className="badge badge-neutral">Mandatory</span>}{" "}
          {r.critical && <span className="badge badge-warn">Critical</span>}{" "}
          {r.evaluable === false && (
            <span
              className="badge badge-warn"
              title="This rule checks a fact no applicant can supply, so live assessments can never answer it."
            >
              Not evaluable - no applicant field
            </span>
          )}
        </>
      ),
    },
    { key: "evidence_text", header: "Evidence", render: (r) => <span className="muted">{r.evidence_text}</span> },
    { key: "status", header: "Status", render: (r) => <StatusBadge value={r.status} /> },
    {
      key: "actions",
      header: "",
      render: (r) => {
        const next = NEXT_ACTION[r.status];
        return (
          <div className="flex">
            {next && (
              <button
                className="btn btn-sm btn-primary"
                onClick={() => requestAction(next.action, [r.rule_id], next.label)}
              >
                {next.label}
              </button>
            )}
            {(r.status === "VALIDATED" || r.status === "APPROVED") && (
              <button className="btn btn-sm btn-danger" onClick={() => requestAction("reject", [r.rule_id], "Reject")}>
                Reject
              </button>
            )}
            <button
              className="btn btn-sm"
              onClick={() => setTestTarget({ programId: r.program_id, ruleIds: [r.rule_id] })}
            >
              Test
            </button>
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <h1>Rules</h1>
      <div className="form-row">
        <div className="field">
          <label htmlFor="rules-status">Status</label>
          <select id="rules-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s || "All"}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="rules-program">Program</label>
          <input
            id="rules-program"
            value={programId}
            onChange={(e) => setProgramId(e.target.value.toUpperCase())}
            placeholder="e.g. SKILLED_189"
          />
        </div>
        <div className="field" style={{ alignSelf: "flex-end", flex: "0 0 auto" }}>
          <button
            className="btn"
            onClick={() =>
              setTestTarget({ programId, ruleIds: selected.size > 0 ? Array.from(selected) : undefined })
            }
          >
            Test rules
          </button>
        </div>
      </div>

      {testTarget && (
        <RuleTestPanel
          key={`${testTarget.programId}:${(testTarget.ruleIds || []).join(",")}`}
          programId={testTarget.programId}
          ruleIds={testTarget.ruleIds}
          onClose={() => setTestTarget(null)}
        />
      )}

      {selected.size > 0 && (
        <div className="card flex-between">
          <span>{selected.size} selected</span>
          <div className="flex">
            <button className="btn btn-sm" onClick={() => requestBatch("validate")}>
              Validate selected
            </button>
            <button className="btn btn-sm" onClick={() => requestBatch("approve")}>
              Approve selected
            </button>
            <button className="btn btn-sm btn-primary" onClick={() => requestBatch("activate")}>
              Activate selected
            </button>
          </div>
        </div>
      )}

      <DataTable
        columns={columns}
        rows={rulesState.data}
        rowKey={(r) => r.rule_id}
        emptyTitle="No rules match"
        emptyMessage="Try a different status or program filter."
      />

      {pendingConfirm && (
        <ConfirmDialog
          title={`${pendingConfirm.action === "activate" ? "Activate" : "Retire"} ${pendingConfirm.ruleIds.length} rule${pendingConfirm.ruleIds.length === 1 ? "" : "s"}?`}
          message={
            pendingConfirm.action === "activate"
              ? pendingConfirm.notEvaluable?.length
                ? `${pendingConfirm.notEvaluable.length} of the rules you picked ${pendingConfirm.notEvaluable.length === 1 ? "checks" : "check"} a fact no applicant can supply, so live assessments will always return UNKNOWN for ${pendingConfirm.notEvaluable.length === 1 ? "it" : "them"}. Activating anyway makes the gap permanent; type ACTIVATE to confirm you mean it.`
                : "Activating supersedes any currently active rule with the same program/category/field and immediately affects live eligibility assessments."
              : "Retiring removes this rule from live eligibility assessments."
          }
          confirmLabel={pendingConfirm.action === "activate" ? "Activate" : "Retire"}
          danger={pendingConfirm.action === "retire"}
          requireText={pendingConfirm.requireText}
          onConfirm={() => {
            const opts = { confirmNotEvaluable: pendingConfirm.requireText };
            return (pendingConfirm.batch
              ? runBatch(pendingConfirm.action, opts)
              : runAction(pendingConfirm.action, pendingConfirm.ruleIds, opts)
            ).then(() => setPendingConfirm(null));
          }}
          onCancel={() => setPendingConfirm(null)}
        />
      )}
    </div>
  );
}
