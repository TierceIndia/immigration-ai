import { useState } from "react";
import Modal from "../components/Modal.jsx";
import * as api from "../api/client.js";

const SOURCE_ID_RE = /^SRC-[A-Z0-9][A-Z0-9_-]*$/;
const VISA_CATEGORIES = ["SKILLED", "WORK", "STUDENT", "VISITOR"];

function isHttpsUrl(value) {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

export default function AddSourceForm({ knownPrograms, onClose, onCreated }) {
  const [form, setForm] = useState({
    source_id: "",
    country_id: "",
    program_id: "",
    visa_category: "SKILLED",
    authority: "",
    url: "",
    source_type: "HTML",
    priority: "PRIMARY",
    crawl_frequency: "DAILY",
    notes: "",
  });
  const [errors, setErrors] = useState({});
  const [submitError, setSubmitError] = useState(null);
  const [busy, setBusy] = useState(false);

  function set(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function applyKnownProgram(programId) {
    const known = knownPrograms.find((p) => p.program_id === programId);
    set("program_id", programId);
    if (known) {
      set("country_id", known.country_id);
      set("visa_category", known.visa_category);
    }
  }

  function validate() {
    const next = {};
    if (!SOURCE_ID_RE.test(form.source_id)) {
      next.source_id = "Must look like SRC-AU-189-MAIN (starts with SRC-, letters/digits/hyphens only)";
    }
    if (!form.country_id.trim()) next.country_id = "Required";
    if (!form.program_id.trim()) next.program_id = "Required";
    if (!form.authority.trim()) next.authority = "Required";
    if (!isHttpsUrl(form.url)) next.url = "Must be a valid https:// URL";
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!validate()) return;
    setBusy(true);
    setSubmitError(null);
    try {
      await api.createSource({ ...form, notes: form.notes || undefined });
      onCreated();
    } catch (err) {
      setSubmitError(err.message || "Could not create source");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Add source" onClose={onClose}>
      <form onSubmit={handleSubmit} noValidate>
        <div className="field">
          <label htmlFor="src-id">Source ID</label>
          <input
            id="src-id"
            className={errors.source_id ? "invalid" : ""}
            value={form.source_id}
            onChange={(e) => set("source_id", e.target.value.toUpperCase())}
            placeholder="SRC-AU-189-MAIN"
          />
          {errors.source_id && <span className="field-error">{errors.source_id}</span>}
        </div>

        {knownPrograms.length > 0 && (
          <div className="field">
            <label htmlFor="src-known-program">Known program (optional shortcut)</label>
            <select id="src-known-program" onChange={(e) => e.target.value && applyKnownProgram(e.target.value)} defaultValue="">
              <option value="">— pick to autofill —</option>
              {knownPrograms.map((p) => (
                <option key={p.program_id} value={p.program_id}>
                  {p.program_id} ({p.visa_category})
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="form-row">
          <div className="field">
            <label htmlFor="src-country">Country ID</label>
            <input
              id="src-country"
              className={errors.country_id ? "invalid" : ""}
              value={form.country_id}
              onChange={(e) => set("country_id", e.target.value.toUpperCase())}
              placeholder="AU"
            />
            {errors.country_id && <span className="field-error">{errors.country_id}</span>}
          </div>
          <div className="field">
            <label htmlFor="src-program">Program ID</label>
            <input
              id="src-program"
              className={errors.program_id ? "invalid" : ""}
              value={form.program_id}
              onChange={(e) => set("program_id", e.target.value.toUpperCase())}
              placeholder="SKILLED_189"
            />
            {errors.program_id && <span className="field-error">{errors.program_id}</span>}
          </div>
        </div>

        <div className="field">
          <label htmlFor="src-visa-category">Visa category</label>
          <select id="src-visa-category" value={form.visa_category} onChange={(e) => set("visa_category", e.target.value)}>
            {VISA_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="src-authority">Authority</label>
          <input
            id="src-authority"
            className={errors.authority ? "invalid" : ""}
            value={form.authority}
            onChange={(e) => set("authority", e.target.value)}
            placeholder="Home Affairs"
          />
          {errors.authority && <span className="field-error">{errors.authority}</span>}
        </div>

        <div className="field">
          <label htmlFor="src-url">URL</label>
          <input
            id="src-url"
            className={errors.url ? "invalid" : ""}
            value={form.url}
            onChange={(e) => set("url", e.target.value)}
            placeholder="https://immi.homeaffairs.gov.au/..."
          />
          {errors.url && <span className="field-error">{errors.url}</span>}
        </div>

        <div className="form-row">
          <div className="field">
            <label htmlFor="src-type">Source type</label>
            <select id="src-type" value={form.source_type} onChange={(e) => set("source_type", e.target.value)}>
              <option value="HTML">HTML</option>
              <option value="PDF">PDF</option>
              <option value="API">API</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="src-priority">Priority</label>
            <select id="src-priority" value={form.priority} onChange={(e) => set("priority", e.target.value)}>
              <option value="PRIMARY">PRIMARY</option>
              <option value="SECONDARY">SECONDARY</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="src-frequency">Crawl frequency</label>
            <select id="src-frequency" value={form.crawl_frequency} onChange={(e) => set("crawl_frequency", e.target.value)}>
              <option value="DAILY">DAILY</option>
              <option value="WEEKLY">WEEKLY</option>
              <option value="MONTHLY">MONTHLY</option>
            </select>
          </div>
        </div>

        <div className="field">
          <label htmlFor="src-notes">Notes (optional)</label>
          <textarea id="src-notes" rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} />
        </div>

        {submitError && <p className="field-error">{submitError}</p>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Creating…" : "Create source"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
