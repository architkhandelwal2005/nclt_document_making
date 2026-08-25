import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check, Download, FileCheck2, FileText, Send, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";

const draftFields = [
  ["corporate_debtor_name", "Corporate debtor name", true], ["date_of_incorporation", "Date of incorporation", true, "date"],
  ["registration_authority", "Registration authority / RoC location", true], ["cin", "CIN", true],
  ["registered_and_principal_address", "Registered and principal office address", true, "textarea", "full"],
  ["cirp_commencement_date", "CIRP commencement date", true, "date"], ["order_upload_date", "Order upload / receipt date", false, "date"],
  ["estimated_closure_date", "Estimated CIRP closure date", true, "date"],
  ["irp_name", "IRP name", true], ["irp_registration_number", "IRP registration number", true],
  ["irp_registered_address", "IRP address registered with IBBI", true, "textarea", "full"],
  ["irp_registered_email", "IRP registered email", true, "email"],
  ["correspondence_address", "Correspondence address", true, "textarea", "full"],
  ["process_specific_email", "Process-specific email", true, "email"],
  ["claims_submission_last_date", "Last date for claims", true, "date"],
  ["creditor_classes", "Classes of creditors", false, "textarea", "full"],
  ["authorised_representatives", "Proposed authorised representatives", false, "textarea", "full"],
  ["forms_weblink", "Relevant forms web link", true],
  ["authorised_representative_details", "Authorised representative details", false, "textarea", "full"],
  ["afa_valid_until", "AFA valid until", false, "date"], ["announcement_date", "Form A date", true, "date"],
  ["announcement_place", "Form A place", true],
];

const publishedFields = [
  ["publication_date", "Publication date", "date"], ["newspaper_name", "Newspaper name"],
  ["publication_language", "Publication language"], ["edition_or_place", "Edition / place"],
  ["corporate_debtor_name", "Corporate debtor name"], ["cin", "CIN"],
  ["cirp_commencement_date", "CIRP commencement date", "date"],
  ["claims_submission_last_date", "Claims submission last date", "date"],
  ["irp_name", "IRP name"], ["irp_registration_number", "IRP registration number"],
  ["process_specific_email", "Process-specific email", "email"],
];

function Field({ spec, values, setValues, provenance, disabled = false }) {
  const [key, label, required, type = "text", width = ""] = spec;
  const source = provenance?.[key]?.source_type?.replaceAll("_", " ");
  return <label className={width}>{label}{type === "textarea" ?
    <textarea disabled={disabled} required={required} value={values[key] || ""} onChange={event => setValues({ ...values, [key]: event.target.value })} /> :
    <input disabled={disabled} required={required} type={type} value={values[key] || ""} onChange={event => setValues({ ...values, [key]: event.target.value })} />}
    {source && <small>Source: {source}</small>}
  </label>;
}

export default function PublicAnnouncementWorkspace({ caseRecord }) {
  const caseId = caseRecord.id;
  const [record, setRecord] = useState(null);
  const [draft, setDraft] = useState({});
  const [published, setPublished] = useState({});
  const [publishedFile, setPublishedFile] = useState(null);
  const [acceptConflicts, setAcceptConflicts] = useState(false);
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    try {
      const { data } = await api.get(`/cases/${caseId}/public-announcement`);
      setRecord(data); setDraft(data.draft_data || {}); setPublished(data.published_review || {});
    } catch (error) { toast.error(errorMessage(error, "Could not load the Public Announcement workflow.")); }
  }, [caseId]);

  useEffect(() => { load(); }, [load]);
  const status = record?.status || "NOT_STARTED";
  const conflicts = record?.conflicts || [];
  const warnings = record?.extraction?.warnings || [];
  const hasDraftDocument = Boolean(record?.draft_document_id);
  const hasPublishedDocument = Boolean(record?.published_document_id);
  const reviewed = ["PUBLISHED_REVIEWED", "PUBLISHED"].includes(status);
  const confirmed = status === "PUBLISHED";
  const draftLocked = ["PUBLISHED_UPLOADED", "PUBLISHED_REVIEWED", "PUBLISHED"].includes(status);

  const run = async (name, action, message) => {
    setBusy(name);
    try { const { data } = await action(); setRecord(current => ({ ...current, ...data })); setDraft(data.draft_data || draft); setPublished(data.published_review || published); toast.success(message); }
    catch (error) { toast.error(errorMessage(error)); }
    finally { setBusy(""); }
  };

  const saveDraft = event => { event.preventDefault(); run("save", () => api.put(`/cases/${caseId}/public-announcement/draft`, { values: draft }), "Form A draft saved"); };
  const generate = () => run("generate", () => api.post(`/cases/${caseId}/public-announcement/generate`, { values: draft }), "Editable Form A generated and stored in Documents");
  const setStage = stage => run(stage, () => api.post(`/cases/${caseId}/public-announcement/${stage}`), stage === "ready" ? "Form A marked ready for publication" : "Form A marked sent for publication");

  const uploadPublished = async event => {
    event.preventDefault(); if (!publishedFile) return;
    setBusy("upload");
    const body = new FormData(); body.append("file", publishedFile);
    try {
      const { data } = await api.post(`/cases/${caseId}/public-announcement/published`, body);
      setRecord(current => ({ ...current, ...data })); setPublished(data.published_review || {}); setPublishedFile(null);
      event.target.reset(); toast.success("Published copy uploaded. Review every extracted value before confirmation.");
    } catch (error) { toast.error(errorMessage(error, "Could not upload the published announcement.")); }
    finally { setBusy(""); }
  };

  const savePublishedReview = event => {
    event.preventDefault();
    run("review", () => api.put(`/cases/${caseId}/public-announcement/published-review`, { values: published }), "Published Announcement review saved");
  };
  const confirmPublished = () => {
    if (!window.confirm("Confirm this published announcement and start claims/deadline downstream automation? This action uses the reviewed publication fields as the operational source.")) return;
    run("confirm", () => api.post(`/cases/${caseId}/public-announcement/confirm`, { accept_conflicts: acceptConflicts }), "Published Announcement confirmed; downstream workflow started");
  };

  const download = async (documentId, fallback) => {
    try {
      const response = await api.get(`/cases/${caseId}/documents/${documentId}/file`, { responseType: "blob" });
      const url = URL.createObjectURL(response.data); const link = document.createElement("a");
      link.href = url; link.download = fallback; link.click(); URL.revokeObjectURL(url);
    } catch (error) { toast.error(errorMessage(error, "Could not download the document.")); }
  };

  const steps = useMemo(() => [
    ["Admission Order", Boolean(record?.admission_order_confirmed), record?.admission_order_confirmed ? "Uploaded and confirmed" : "Confirmation required before Form A generation"],
    ["Draft Form A", hasDraftDocument, hasDraftDocument ? "Generated and retained" : "Awaiting generation"],
    ["Sent for Publication", ["SENT_FOR_PUBLICATION", "PUBLISHED_UPLOADED", "PUBLISHED_REVIEWED", "PUBLISHED"].includes(status), ["SENT_FOR_PUBLICATION", "PUBLISHED_UPLOADED", "PUBLISHED_REVIEWED", "PUBLISHED"].includes(status) ? "Marked sent" : "Pending"],
    ["Published Copy", hasPublishedDocument, hasPublishedDocument ? "Uploaded" : "Awaiting newspaper"],
    ["Reviewed", reviewed, reviewed ? "Mandatory review completed" : "Pending"],
    ["Confirmed", confirmed, confirmed ? "Downstream workflow active" : "Not triggered"],
  ], [confirmed, hasDraftDocument, hasPublishedDocument, record?.admission_order_confirmed, reviewed, status]);

  if (!record) return <section className="casefile-panel panel-empty">Loading Public Announcement workflow…</section>;

  return <div className="space-y-6" data-testid="public-announcement-workflow">
    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">TWO-STAGE CONTROL</p><h3>Form A preparation → published evidence</h3></div><FileCheck2 size={20} /></div>
      <div className="pa-flow">{steps.map(([label, done, detail]) => <span className={done ? "complete" : "pending"} key={label}><b>{done ? "✓" : "○"} {label}</b><small>{detail}</small></span>)}</div>
      <p className="pa-gate-note">Generating Form A does not start claims or CoC downstream automation. That begins only after the separately uploaded publication is reviewed and confirmed.</p>
    </section>

    <form className="casefile-panel" onSubmit={saveDraft}>
      <div className="panel-heading"><div><p className="kicker">STAGE 1 · EDITABLE INPUT</p><h3>Draft Public Announcement — Form A</h3></div><FileText size={20} /></div>
      <div className="form-grid">{draftFields.map(spec => <Field key={spec[0]} spec={spec} values={draft} setValues={setDraft} provenance={record.provenance} disabled={draftLocked} />)}</div>
      <div className="coc-action-row"><button className="outline-button" disabled={busy || draftLocked}><Check size={16} />Save editable draft</button><button type="button" className="primary-button" onClick={generate} disabled={busy || draftLocked}><FileText size={16} />Generate Form A DOCX</button>{hasDraftDocument && <button type="button" className="outline-button" onClick={() => download(record.draft_document_id, "Public_Announcement_Form_A.docx")}><Download size={16} />Download Form A</button>}<button type="button" className="outline-button" onClick={() => setStage("ready")} disabled={busy || !hasDraftDocument || draftLocked || status !== "DRAFT"}><FileCheck2 size={16} />Ready for publication</button><button type="button" className="primary-button" onClick={() => setStage("sent")} disabled={busy || status !== "READY_FOR_PUBLICATION"}><Send size={16} />Mark sent</button></div>
      <small>The generated DOCX reproduces only the clean Form A portion of the supplied reference. Newspaper pages are never used as generation templates.</small>
    </form>

    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">STAGE 2 · NEWSPAPER RETURN</p><h3>Upload Published Public Announcement</h3></div><Upload size={20} /></div>
      <form className="upload-form" onSubmit={uploadPublished}><input type="file" accept=".pdf,application/pdf" required onChange={event => setPublishedFile(event.target.files?.[0] || null)} /><small>Upload the actual PDF returned by the newspaper or publication agency. It remains separate from Form A.</small><button className="outline-button" disabled={!publishedFile || busy === "upload"}><Upload size={16} />Extract and review</button></form>
      {hasPublishedDocument && <button className="outline-button" onClick={() => download(record.published_document_id, "Published_Public_Announcement.pdf")}><Download size={16} />View published copy</button>}
    </section>

    {hasPublishedDocument && <form className="casefile-panel" onSubmit={savePublishedReview}>
      <div className="panel-heading"><div><p className="kicker">MANDATORY HUMAN REVIEW</p><h3>Published Announcement particulars</h3></div><FileCheck2 size={20} /></div>
      <p className="pa-extraction-note">Extraction method: <b>{record.extraction_method?.replaceAll("_", " ") || "manual review"}</b>. Every value remains editable and no extracted value is committed automatically.</p>
      {warnings.map(warning => <div className="pa-warning" key={warning}><AlertTriangle size={17} />{warning}</div>)}
      <div className="form-grid">{publishedFields.map(([key, label, type = "text"]) => <label key={key}>{label}<input type={type} value={published[key] || ""} onChange={event => setPublished({ ...published, [key]: event.target.value })} /></label>)}</div>
      {conflicts.length > 0 && <div className="pa-conflicts"><h4><AlertTriangle size={17} /> Published announcement differs from existing case information</h4>{conflicts.map(item => <div key={item.field}><b>{item.field.replaceAll("_", " ")}</b><small>Existing value: {item.existing_value || "—"}</small><small>Published value: {item.published_value || "—"}</small></div>)}</div>}
      <div className="coc-action-row"><button className="outline-button" disabled={busy}><Check size={16} />Save mandatory review</button>{reviewed && <button type="button" className="primary-button" onClick={confirmPublished} disabled={busy || (conflicts.length > 0 && !acceptConflicts)}><FileCheck2 size={16} />Confirm Published Announcement</button>}</div>
      {reviewed && conflicts.length > 0 && <label className="pa-confirm"><input type="checkbox" checked={acceptConflicts} onChange={event => setAcceptConflicts(event.target.checked)} />I explicitly confirm the published values despite the differences shown above.</label>}
      {confirmed && <p className="pa-confirmed">Confirmed {record.confirmed_at?.slice(0, 10)}. Publication date and claims deadline now drive the applicable case workflow.</p>}
    </form>}
  </div>;
}
