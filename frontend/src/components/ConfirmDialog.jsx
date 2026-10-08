import { useState } from "react";
import Modal from "./Modal.jsx";

// Used for any action that changes live assessments (activate/retire) or
// acts on multiple selected items at once - always states the count.
export default function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  onConfirm,
  onCancel,
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleConfirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(err.message || "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <Modal title={title} onClose={onCancel}>
      <p>{message}</p>
      {error && <p className="field-error">{error}</p>}
      <div className="modal-actions">
        <button className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          className={danger ? "btn btn-danger" : "btn btn-primary"}
          onClick={handleConfirm}
          disabled={busy}
        >
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
