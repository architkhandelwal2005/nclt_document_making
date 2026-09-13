import React, { useEffect, useMemo, useState } from "react";
import { Bug, ClipboardCheck, Save } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";

const blankReport = { case_id: "", module: "", action_taken: "", expected_result: "", actual_result: "", severity: "MEDIUM" };
const triageRoles = new Set(["admin", "administrator", "professional"]);
const statuses = ["NEW", "TRIAGED", "IN_PROGRESS", "RESOLVED", "DEFERRED"];

function timestamp(value) {
  return value ? new Date(value).toLocaleString() : "—";
}

export default function UatFeedbackView({ user, cases, environment }) {
  const [report, setReport] = useState(blankReport);
  const [reports, setReports] = useState([]);
  const [triage, setTriage] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const canTriage = triageRoles.has(user.role);
  const caseOptions = useMemo(() => [...cases].sort((a, b) => a.name.localeCompare(b.name)), [cases]);

  const load = async () => {
    try {
      const { data } = await api.get("/uat-feedback");
      setReports(data);
      setTriage(Object.fromEntries(data.map(item => [item.id, { status: item.status, triage_notes: item.triage_notes || "" }])));
    } catch (error) {
      toast.error(errorMessage(error, "Could not load UAT feedback."));
    }
  };

  useEffect(() => { load(); }, []);

  const submit = async event => {
    event.preventDefault();
    setSubmitting(true);
    try {
      await api.post("/uat-feedback", { ...report, case_id: report.case_id || null });
      setReport(blankReport);
      toast.success("UAT report saved for triage");
      load();
    } catch (error) {
      toast.error(errorMessage(error, "Could not save the UAT report."));
    } finally {
      setSubmitting(false);
    }
  };

  const saveTriage = async id => {
    try {
      const { data } = await api.put(`/uat-feedback/${id}`, triage[id]);
      setReports(items => items.map(item => item.id === id ? { ...item, ...data } : item));
      toast.success("Triage saved");
    } catch (error) {
      toast.error(errorMessage(error, "Could not save triage."));
    }
  };

  return <section className="workspace uat-feedback-workspace">
    <div className="workspace-intro">
      <div>
        <p className="kicker">UAT-1 / TEST FEEDBACK</p>
        <h2>Report a testing issue</h2>
        <p className="intro-copy">Record the exact action, expected result, and actual result. Do not paste passwords, client documents, personal data, or case evidence into this form.</p>
      </div>
      <div className="uat-feedback-environment"><ClipboardCheck size={18} /><span>{environment || "UNKNOWN"} environment</span></div>
    </div>

    <section className="casefile-panel uat-feedback-form-panel">
      <div className="panel-heading"><div><p className="kicker">REPRODUCIBLE REPORT</p><h3>What happened?</h3></div><Bug size={21} /></div>
      <form className="module-create-form" onSubmit={submit}>
        <div className="form-grid">
          <label>Company / case <select value={report.case_id} onChange={event => setReport({ ...report, case_id: event.target.value })}><option value="">No case selected</option>{caseOptions.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label>Module <input required minLength="2" maxLength="100" placeholder="For example: Claims Register" value={report.module} onChange={event => setReport({ ...report, module: event.target.value })} /></label>
          <label>Severity <select value={report.severity} onChange={event => setReport({ ...report, severity: event.target.value })}><option value="LOW">Low — cosmetic or minor inconvenience</option><option value="MEDIUM">Medium — workflow slowed</option><option value="HIGH">High — key workflow blocked</option><option value="BLOCKER">Blocker — testing cannot continue</option></select></label>
        </div>
        <label className="full-width-field">Action taken <textarea required minLength="5" maxLength="1500" placeholder="Numbered steps are best: 1. Open… 2. Click…" value={report.action_taken} onChange={event => setReport({ ...report, action_taken: event.target.value })} /></label>
        <label className="full-width-field">Expected result <textarea required minLength="5" maxLength="1500" value={report.expected_result} onChange={event => setReport({ ...report, expected_result: event.target.value })} /></label>
        <label className="full-width-field">Actual result / exact error <textarea required minLength="5" maxLength="1500" value={report.actual_result} onChange={event => setReport({ ...report, actual_result: event.target.value })} /></label>
        <div className="modal-actions"><button className="primary-button" disabled={submitting}><Bug size={16} />{submitting ? "Saving…" : "Save UAT report"}</button></div>
      </form>
    </section>

    <section className="casefile-panel uat-feedback-list-panel">
      <div className="panel-heading"><div><p className="kicker">{canTriage ? "FIRM UAT QUEUE" : "MY REPORTS"}</p><h3>{canTriage ? "Reports awaiting review" : "Reports you submitted"}</h3></div></div>
      {reports.length === 0 ? <p className="muted-hint">No UAT reports have been recorded yet.</p> : <div className="uat-report-list">{reports.map(item => <article className="uat-report" key={item.id}>
        <header><div><span className={`uat-severity severity-${item.severity.toLowerCase()}`}>{item.severity}</span><span className="uat-status">{item.status.replaceAll("_", " ")}</span></div><small>{timestamp(item.created_at)} · {item.reporter_name}</small></header>
        <h4>{item.module}{item.case_name ? ` · ${item.case_name}` : ""}</h4>
        <dl><div><dt>Action</dt><dd>{item.action_taken}</dd></div><div><dt>Expected</dt><dd>{item.expected_result}</dd></div><div><dt>Actual</dt><dd>{item.actual_result}</dd></div></dl>
        {canTriage ? <div className="uat-triage"><label>Status <select value={triage[item.id]?.status || item.status} onChange={event => setTriage(current => ({ ...current, [item.id]: { ...current[item.id], status: event.target.value } }))}>{statuses.map(status => <option key={status} value={status}>{status.replaceAll("_", " ")}</option>)}</select></label><label>Internal triage note <textarea maxLength="1500" value={triage[item.id]?.triage_notes || ""} onChange={event => setTriage(current => ({ ...current, [item.id]: { ...current[item.id], triage_notes: event.target.value } }))} /></label><button className="outline-button small" onClick={() => saveTriage(item.id)}><Save size={14} />Save triage</button></div> : item.triage_notes ? <p className="uat-triage-note"><b>Triage update:</b> {item.triage_notes}</p> : null}
      </article>)}</div>}
    </section>
  </section>;
}
