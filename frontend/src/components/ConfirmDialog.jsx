import { useState } from "react";
import Modal from "./Modal.jsx";

// Used for any action that changes live assessments (activate/retire) or
// acts on multiple selected items at once - always states the count.
// `requireText` asks the admin to type an exact word before the confirm
// button unlocks. It is reserved for actions whose effect an ordinary click
// hides - activating a rule nothing can ever answer, for example.
export default function ConfirmDialog({
  title,
  message,
  confirmLabel = "Confirm",
  danger = false,
  requireText,
  onConfirm,
  onCancel,
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [typed, setTyped] = useState("");
  const canConfirm = !requireText || typed.trim() === requireText;

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
      {requireText && (
        <div className="field">
          <label htmlFor="confirm-text">Type {requireText} to confirm</label>
          <input
            id="confirm-text"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
            autoFocus
          />
        </div>
      )}
      {error && <p className="field-error">{error}</p>}
      <div className="modal-actions">
        <button className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          className={danger ? "btn btn-danger" : "btn btn-primary"}
          onClick={handleConfirm}
          disabled={busy || !canConfirm}
        >
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </Modal>
  );
}
