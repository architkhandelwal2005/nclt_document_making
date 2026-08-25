import React, { useState } from "react";
import { FileSearch, Upload, X, Eraser } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { toast } from "sonner";

const caseFields = [
  ["process_type", "Process"], ["admission_section", "IBC section"], ["tribunal", "Tribunal"],
  ["nclt_bench", "Bench"], ["court_number", "Court number"], ["petition_number", "Case number"],
  ["order_date", "Order date", "date"], ["commencement_date", "CIRP commencement date", "date"], ["order_upload_date", "Order upload date", "date"],
];
const corporateDebtorFields = [["name", "Corporate debtor name"], ["cin", "Corporate debtor CIN"], ["registered_address", "Registered office address", "textarea"]];
const applicantFields = [["name", "Applicant / financial creditor name"], ["cin", "Applicant CIN"], ["address", "Registered office address", "textarea"], ["branch_address", "Branch / secondary address", "textarea"]];
const irpFields = [["name", "Appointed IRP name"], ["registration_number", "IBBI registration"], ["address", "Address", "textarea"], ["email", "Email"], ["form_2_date", "Form 2 date", "date"], ["afa_valid_until", "AFA valid until", "date"]];

function FieldGrid({ title, value, fields, onChange, provenance = {} }) {
  return <fieldset className="casefile-panel form-section"><legend>{title}</legend><div className="form-grid">
    {fields.filter(([key]) => Object.prototype.hasOwnProperty.call(value, key)).map(([key, label, type = "text"]) => {
      const source = provenance[key];
      return <label className={type === "textarea" ? "full" : ""} key={key}>{label}
        <span className="review-input-row">{type === "textarea" ? <textarea value={value[key] || ""} onChange={event => onChange(key, event.target.value)} /> : <input type={type} value={value[key] || ""} onChange={event => onChange(key, event.target.value)} />}<button type="button" className="field-clear" onClick={() => onChange(key, "")} title={`Clear ${label}`}><Eraser size={14} /></button></span>
        {source && <small className="source-note">{source.page ? `Page ${source.page}` : "No source match"} · {source.confidence || "NOT_FOUND"} · {source.validation_status || "unvalidated"}{source.source_snippet && <span className="source-snippet">“{source.source_snippet}”</span>}</small>}
      </label>;
    })}
  </div></fieldset>;
}

export default function AdmissionOrderIntake({ caseRecord = null, cases = [], onImported }) {
  const [open, setOpen] = useState(Boolean(caseRecord));
  const [file, setFile] = useState(null);
  const [intake, setIntake] = useState(null);
  const [review, setReview] = useState(null);
  const [action, setAction] = useState(caseRecord ? "update" : "create");
  const [targetCaseId, setTargetCaseId] = useState(caseRecord?.id || "");
  const [allowDuplicate, setAllowDuplicate] = useState(false);
  const [reviewAcknowledged, setReviewAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const provenanceFor = prefix => Object.fromEntries(Object.entries(intake?.extracted?.provenance || {}).filter(([key]) => key.startsWith(`${prefix}.`)).map(([key, value]) => [key.slice(prefix.length + 1), value]));
  const updateSection = (section, key, value) => { setReviewAcknowledged(false); setReview(current => ({ ...current, [section]: { ...current[section], [key]: value } })); };
  const upload = async event => {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    try {
      const body = new FormData(); body.append("file", file); if (caseRecord) body.append("case_id", caseRecord.id);
      const { data } = await api.post("/admission-intakes", body);
      setIntake(data); setReview(data.review); setReviewAcknowledged(false); toast.success("Admission order extracted. Review every proposed value.");
    } catch (error) {
      const message = error?.response?.status === 404
        ? "The running Casefile backend is out of date. Close both Casefile server windows, restart using start_local.bat, and upload again."
        : errorMessage(error, "Could not extract the admission order.");
      toast.error(message, { duration: 9000 });
    }
    finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true);
    try { const { data } = await api.put(`/admission-intakes/${intake.id}`, { review }); setIntake(data); setReview(data.review); toast.success("Review draft saved"); }
    catch (error) { toast.error(errorMessage(error, "Could not save the review.")); }
    finally { setBusy(false); }
  };
  const confirm = async () => {
    if (action === "update" && !targetCaseId) { toast.error("Select the existing company workspace to update."); return; }
    if (!reviewAcknowledged) { toast.error("Review every extracted value and tick the review acknowledgement before importing."); return; }
    setBusy(true);
    try {
      const { data } = await api.post(`/admission-intakes/${intake.id}/confirm`, { review, action, target_case_id: targetCaseId || null, allow_duplicate: allowDuplicate, review_acknowledged: reviewAcknowledged });
      toast.success("Admission order imported into the company workspace"); onImported?.(data.case); setIntake(data.intake); setReview(data.intake.review);
    } catch (error) { toast.error(errorMessage(error, "Could not confirm the import.")); }
    finally { setBusy(false); }
  };
  const reset = () => { setFile(null); setIntake(null); setReview(null); setReviewAcknowledged(false); if (!caseRecord) setOpen(false); };

  if (!open) return <button className="outline-button" onClick={() => setOpen(true)}><FileSearch size={17} />Import admission order</button>;
  return <div className={caseRecord ? "space-y-6" : "modal-backdrop admission-backdrop"}>
    <div className={caseRecord ? "space-y-6" : "admission-modal"}>
      {!caseRecord && <div className="panel-heading"><div><p className="kicker">STAGED INTAKE</p><h3>Import NCLT admission order</h3></div><button className="icon-btn" onClick={reset} aria-label="Close"><X size={18} /></button></div>}
      {!intake && <form className="casefile-panel intake-upload" onSubmit={upload}><FileSearch size={34} /><h3>Upload a searchable admission-order PDF</h3><p>The file is staged first. Nothing is written to a case until you review and confirm.</p><input type="file" accept="application/pdf,.pdf" onChange={event => setFile(event.target.files?.[0] || null)} required /><button className="primary-button" disabled={busy}><Upload size={16} />{busy ? "Extracting…" : "Extract and review"}</button></form>}
      {intake && review && <div className="space-y-6">
        <div className="casefile-panel intake-target"><div><p className="kicker">IMPORT TARGET</p><h3>{action === "create" ? "Create a new company workspace" : `Update: ${cases.find(item => item.id === targetCaseId)?.name || caseRecord?.name || "select a case"}`}</h3><p>This choice controls where all confirmed records will be written.</p></div>{!caseRecord && <div className="target-controls"><select value={action} onChange={event => setAction(event.target.value)}><option value="create">Create new case</option><option value="update">Update existing case</option></select>{action === "update" && <select value={targetCaseId} onChange={event => setTargetCaseId(event.target.value)}><option value="">Select company</option>{cases.map(item => <option key={item.id} value={item.id}>{item.name} · {item.petition_number || "No case number"}</option>)}</select>}</div>}</div>
        {intake.duplicate_candidates?.length > 0 && <div className="intake-warning"><b>Possible duplicate found.</b> {intake.duplicate_candidates.map(item => `${item.name} (${item.petition_number || item.cin || "no identifier"})`).join(", ")}. Select the existing workspace, or {action === "create" && <label><input type="checkbox" checked={allowDuplicate} onChange={event => setAllowDuplicate(event.target.checked)} /> confirm that this must remain a separate case.</label>}</div>}
        {review.case?.cin && !/^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$/.test(review.case.cin) && <div className="intake-warning"><b>CIN requires verification.</b> The extracted value does not match the standard 21-character CIN structure. Compare it with MCA records before confirming.</div>}
        <FieldGrid title="A. Case / proceeding" value={review.case} fields={caseFields} onChange={(key, value) => updateSection("case", key, value)} provenance={provenanceFor("case")} />
        <FieldGrid title="B. Applicant / financial creditor" value={review.applicant} fields={applicantFields} onChange={(key, value) => updateSection("applicant", key, value)} provenance={provenanceFor("applicant")} />
        <FieldGrid title="C. Corporate debtor" value={review.case} fields={corporateDebtorFields} onChange={(key, value) => updateSection("case", key, value)} provenance={provenanceFor("case")} />
        <FieldGrid title="D. Appointed Interim Resolution Professional" value={review.irp} fields={irpFields} onChange={(key, value) => updateSection("irp", key, value)} provenance={provenanceFor("irp")} />
        <details className="casefile-panel candidate-inventory"><summary>All retained extraction candidates</summary>{Object.entries(intake.extracted?.candidates || {}).filter(([, rows]) => rows?.length).map(([group, rows]) => <div key={group}><h4>{group.replaceAll("_", " ")}</h4><ul>{rows.map((item, index) => <li key={`${group}-${index}`}><b>{item.value || "NOT_FOUND"}</b> · {item.role || "unassigned"} · page {item.page || "—"} · {item.confidence}<small>{item.source_snippet}</small></li>)}</ul></div>)}</details>
        <fieldset className="casefile-panel form-section"><legend>Company / MCA master particulars</legend><p className="panel-empty">Capital values may be prefilled from the admission order. Live MCA lookup is not configured, so verify all current particulars independently against MCA records.</p><div className="form-grid">{[["company_status","Company status"],["roc","Registrar of Companies"],["date_of_incorporation","Date of incorporation"],["authorised_capital","Authorised / nominal capital"],["paid_up_capital","Paid-up capital"]].map(([key,label]) => { const source = provenanceFor("mca")[key]; return <label key={key}>{label}<input value={review.mca?.[key] || ""} onChange={event => updateSection("mca", key, event.target.value)} />{source?.page && <small className="source-note">Page {source.page} · stated in admission order; verify against current MCA data</small>}</label>; })}</div></fieldset>
        <fieldset className="casefile-panel form-section"><legend>Referenced documents</legend><div className="review-list">{review.referenced_documents.map((item, index) => <label key={`${item.name}-${index}`}><input type="checkbox" checked={item.selected !== false} onChange={event => setReview(current => ({ ...current, referenced_documents: current.referenced_documents.map((row, rowIndex) => rowIndex === index ? { ...row, selected: event.target.checked } : row) }))} /><span>{item.name}<small>{item.status}</small></span></label>)}</div></fieldset>
        <fieldset className="casefile-panel form-section"><legend>Proposed tasks</legend><div className="review-list">{review.tasks.map((item, index) => <label key={`${item.title}-${index}`}><input type="checkbox" checked={item.selected !== false} onChange={event => setReview(current => ({ ...current, tasks: current.tasks.map((row, rowIndex) => rowIndex === index ? { ...row, selected: event.target.checked } : row) }))} /><span>{item.title}<small>{item.description}</small></span></label>)}</div></fieldset>
        <fieldset className="casefile-panel form-section"><legend>Initial expense contribution</legend><div className="form-grid"><label>Payer<input value={review.contribution.payer_name || ""} onChange={event => updateSection("contribution", "payer_name", event.target.value)} /></label><label>Amount (paise)<input type="number" value={review.contribution.called_amount_paise || 0} onChange={event => updateSection("contribution", "called_amount_paise", Number(event.target.value))} /></label><label>Due date<input type="date" value={review.contribution.due_date || ""} onChange={event => updateSection("contribution", "due_date", event.target.value || null)} /></label><label>Status<input value={review.contribution.status || ""} onChange={event => updateSection("contribution", "status", event.target.value)} /></label><label className="full">Notes<textarea value={review.contribution.notes || ""} onChange={event => updateSection("contribution", "notes", event.target.value)} /></label></div></fieldset>
        {intake.status !== "confirmed" && <label className="casefile-panel review-acknowledgement"><input type="checkbox" checked={reviewAcknowledged} onChange={event => setReviewAcknowledged(event.target.checked)} /><span><b>I reviewed every extracted value and its source evidence.</b><small>Only these confirmed values will be imported and made available to Form A.</small></span></label>}
        <div className="sticky-save"><span>{intake.status === "confirmed" ? "This intake is confirmed and locked." : `${intake.page_count} pages · ${intake.original_filename}`}</span>{intake.status !== "confirmed" && <div className="flex gap-2"><button className="outline-button" disabled={busy} onClick={save}>Save draft</button><button className="primary-button" disabled={busy || !reviewAcknowledged} onClick={confirm}>{busy ? "Working…" : "Confirm reviewed values and import"}</button></div>}</div>
      </div>}
    </div>
  </div>;
}
