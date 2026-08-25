import React, { useState } from "react";
import { ArrowLeft, Save } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { toast } from "sonner";
import CaseModuleManager from "../components/CaseModuleManager";
import ContactsManager from "../components/ContactsManager";
import DocumentsManager from "../components/DocumentsManager";
import AdmissionOrderIntake from "../components/AdmissionOrderIntake";
import ActivityManager from "../components/ActivityManager";
import CocWorkspace from "../components/CocWorkspace";
import PublicAnnouncementWorkspace from "../components/PublicAnnouncementWorkspace";
import NcltOrderFetcher from "../components/NcltOrderFetcher";
import { ClaimChecklist, CocVoting, OrdersAndDirections } from "../components/LinkedWorkflows";
import {
  applicationConfig, assetConfig, claimConfig, cocMeetingConfig, cocMemberConfig,
  communicationConfig, contributionConfig, deadlineConfig, expenseConfig,
  financialConfig, hearingConfig, taskConfig, valuationConfig,
} from "../constants/moduleConfigs";

const tabs = [
  ["overview", "Overview"], ["nclt-fetcher", "NCLT Orders"], ["intake", "Admission Intake"], ["public-announcement", "Public Announcement"], ["tasks", "Tasks"], ["compliance", "Compliance"],
  ["hearings", "Hearings & Applications"], ["claims", "Claims"], ["coc", "CoC"],
  ["contacts", "Contacts"], ["documents", "Documents"], ["communications", "Communications"],
  ["assets", "Assets & Finance"], ["valuations", "Valuation"], ["expenses", "Expenses"],
  ["activity", "Activity"],
];

const fieldGroups = [
  ["Company information", [
    ["name", "Corporate debtor name", "text", true], ["cin", "CIN", "text"],
    ["registered_email", "Registered email", "email"], ["industry", "Industry", "text"],
    ["registered_address", "Registered office address", "textarea", false, "full"],
  ]],
  ["Proceeding information", [
    ["nclt_bench", "NCLT bench", "text"], ["petition_number", "Petition / application number", "text"],
    ["applicant_name", "Applicant / petitioning creditor", "text"], ["applicant_category", "Applicant category", "select", false, "", ["", "Financial creditor", "Operational creditor", "Corporate applicant", "Other"]],
    ["order_date", "Admission / order date", "date"], ["commencement_date", "Insolvency commencement date", "date"],
  ]],
  ["Process control", [
    ["internal_reference", "Internal reference", "text"], ["process_type", "Process type", "select", false, "", ["CIRP", "Liquidation", "Voluntary Liquidation", "Pre-packaged Insolvency", "NCLT Litigation"]],
    ["status", "Case status", "select", false, "", ["active", "on-hold", "closed"]], ["current_stage", "Current stage", "text"],
    ["risk_level", "Risk level", "select", false, "", ["normal", "watch", "high", "critical"]], ["closure_date", "Closure date", "date"],
    ["outcome", "Outcome", "text", false, "full"], ["notes", "Internal notes", "textarea", false, "full"],
  ]],
];

function CaseOverview({ caseRecord, onUpdated }) {
  const [form, setForm] = useState(caseRecord);
  const [report, setReport] = useState({});
  const [busy, setBusy] = useState(false);
  React.useEffect(() => { setForm(caseRecord); api.get(`/cases/${caseRecord.id}/report`).then(({ data }) => setReport(data)).catch(() => {}); }, [caseRecord]);
  const save = async e => {
    e.preventDefault(); setBusy(true);
    try {
      const payload = { ...form, order_date: form.order_date || null, commencement_date: form.commencement_date || null, closure_date: form.closure_date || null };
      const { data } = await api.put(`/cases/${caseRecord.id}`, payload);
      onUpdated(data); toast.success("Case master saved");
    } catch (error) { toast.error(errorMessage(error, "Could not save the case master.")); }
    finally { setBusy(false); }
  };
  return <form className="space-y-6" onSubmit={save} data-testid="case-master-form"><div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-3">{[["Open tasks", report.open_tasks], ["Open deadlines", report.open_deadlines], ["Claims", report.claims], ["Documents", report.documents], ["Hearings", report.hearings], ["Expenses", report.expense_total]].map(([name, value]) => <div className="case-mini-stat" key={name}><span>{name}</span><b>{value || 0}</b></div>)}</div>
    {fieldGroups.map(([heading, fields]) => <fieldset className="casefile-panel form-section" key={heading}><legend>{heading}</legend><div className="form-grid">{fields.map(([key, label, type, required, width, options]) => <label className={width || ""} key={key}>{label}{type === "textarea" ? <textarea required={required} value={form[key] || ""} onChange={e => setForm({ ...form, [key]: e.target.value })} /> : type === "select" ? <select required={required} value={form[key] || ""} onChange={e => setForm({ ...form, [key]: e.target.value })}>{options.map(option => <option key={option} value={option}>{option || "Select"}</option>)}</select> : <input type={type} required={required} value={form[key] || ""} onChange={e => setForm({ ...form, [key]: e.target.value })} />}</label>)}</div></fieldset>)}
    <div className="sticky-save"><span>Changes affect this company only.</span><button className="primary-button" disabled={busy} data-testid="save-case-master-button"><Save size={16} />{busy ? "Saving…" : "Save case master"}</button></div>
  </form>;
}

function Manager({ caseId, config }) {
  return <CaseModuleManager caseId={caseId} {...config} />;
}

function ModuleContent({ tab, caseRecord, onUpdated }) {
  const caseId = caseRecord.id;
  if (tab === "nclt-fetcher") return <NcltOrderFetcher caseRecord={caseRecord} />;
  if (tab === "intake") return <AdmissionOrderIntake caseRecord={caseRecord} cases={[caseRecord]} onImported={onUpdated} />;
  if (tab === "public-announcement") return <PublicAnnouncementWorkspace caseRecord={caseRecord} />;
  if (tab === "tasks") return <Manager caseId={caseId} config={taskConfig} />;
  if (tab === "compliance") return <Manager caseId={caseId} config={deadlineConfig} />;
  if (tab === "hearings") return <div className="space-y-6"><Manager caseId={caseId} config={hearingConfig} /><Manager caseId={caseId} config={applicationConfig} /><OrdersAndDirections caseId={caseId} /></div>;
  if (tab === "claims") return <div className="space-y-6"><Manager caseId={caseId} config={claimConfig} /><ClaimChecklist caseId={caseId} /></div>;
  if (tab === "coc") return <div className="space-y-6"><Manager caseId={caseId} config={cocMemberConfig} /><Manager caseId={caseId} config={cocMeetingConfig} /><CocWorkspace caseRecord={caseRecord} /><CocVoting caseId={caseId} /></div>;
  if (tab === "contacts") return <ContactsManager caseId={caseId} />;
  if (tab === "documents") return <DocumentsManager caseRecord={caseRecord} />;
  if (tab === "communications") return <Manager caseId={caseId} config={communicationConfig} />;
  if (tab === "assets") return <div className="space-y-6"><Manager caseId={caseId} config={assetConfig} /><Manager caseId={caseId} config={financialConfig} /></div>;
  if (tab === "valuations") return <Manager caseId={caseId} config={valuationConfig} />;
  if (tab === "expenses") return <div className="space-y-6"><Manager caseId={caseId} config={expenseConfig} /><Manager caseId={caseId} config={contributionConfig} /></div>;
  if (tab === "activity") return <ActivityManager caseId={caseId} />;
  return null;
}

export default function CaseWorkspace({ caseRecord, onBack, onUpdated }) {
  const [tab, setTab] = useState("overview");
  const label = tabs.find(item => item[0] === tab)?.[1] || "Case";
  return <section className="case-workspace" data-testid="case-workspace">
    <header className="case-context-header"><button className="icon-btn" onClick={onBack} aria-label="Return to firm dashboard"><ArrowLeft size={19} /></button><div><p className="kicker">SELECTED COMPANY / {caseRecord.process_type}</p><h2>{caseRecord.name}</h2><p>{caseRecord.petition_number || "Petition number not set"} · {caseRecord.nclt_bench || "Bench not set"}</p></div><span className={`case-risk ${caseRecord.risk_level}`}>{caseRecord.risk_level} risk</span></header>
    <nav className="case-tabs" aria-label="Case modules">{tabs.map(([id, name]) => <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)} data-testid={`case-tab-${id}`}>{name}</button>)}</nav>
    <div className="case-module"><div className="module-heading"><p className="kicker">CASE MODULE</p><h2>{label}</h2></div>{tab === "overview" ? <CaseOverview caseRecord={caseRecord} onUpdated={onUpdated} /> : <ModuleContent tab={tab} caseRecord={caseRecord} onUpdated={onUpdated} />}</div>
  </section>;
}
