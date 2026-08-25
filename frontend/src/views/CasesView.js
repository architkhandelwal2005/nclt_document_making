import React, { useState } from "react";
import { ArrowRight, Plus, Search, X } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { toast } from "sonner";
import AdmissionOrderIntake from "../components/AdmissionOrderIntake";

const blank = { name: "", internal_reference: "", cin: "", process_type: "CIRP", nclt_bench: "", petition_number: "", order_date: "", commencement_date: "", current_stage: "Commencement", risk_level: "normal" };

export default function CasesView({ cases, setCases, onOpenCase, startCreating = false, onCreatingChange }) {
  const [query, setQuery] = useState("");
  const [showForm, setShowForm] = useState(startCreating);
  const [form, setForm] = useState(blank);
  const [busy, setBusy] = useState(false);
  React.useEffect(() => setShowForm(startCreating), [startCreating]);
  const close = () => { setShowForm(false); onCreatingChange?.(false); setForm(blank); };
  const create = async e => {
    e.preventDefault(); setBusy(true);
    try {
      const payload = { ...form, order_date: form.order_date || null, commencement_date: form.commencement_date || null };
      const { data } = await api.post("/cases", payload);
      setCases(list => [data, ...list]); toast.success("Case workspace created"); close(); onOpenCase(data);
    } catch (error) { toast.error(errorMessage(error, "Could not create the case.")); }
    finally { setBusy(false); }
  };
  const filtered = cases.filter(item => `${item.name} ${item.cin} ${item.petition_number}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <section className="workspace space-y-7" data-testid="cases-view">
      <div className="workspace-intro"><div><p className="kicker">CASE PORTFOLIO</p><h2>Select a company workspace.</h2><p className="intro-copy">Operational records remain isolated inside the selected company.</p></div><div className="flex gap-2"><AdmissionOrderIntake cases={cases} onImported={record => { setCases(list => [record, ...list.filter(item => item.id !== record.id)]); onOpenCase(record); }} /><button className="primary-button" onClick={() => setShowForm(true)} data-testid="new-case-button"><Plus size={17} />New case</button></div></div>
      <label className="case-search"><Search size={16} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search company, CIN, or petition number" aria-label="Search cases" /></label>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {filtered.map(item => <button className="case-card" key={item.id} onClick={() => onOpenCase(item)} data-testid={`case-card-${item.id}`}><div className="flex justify-between gap-4"><span className={`risk-mark ${item.risk_level}`} /><span className="status-chip">{item.status}</span></div><h3>{item.name}</h3><p>{item.petition_number || "Petition number not set"}</p><dl><div><dt>Process</dt><dd>{item.process_type}</dd></div><div><dt>Stage</dt><dd>{item.current_stage || "Not set"}</dd></div><div><dt>Bench</dt><dd>{item.nclt_bench || "Not set"}</dd></div></dl><span className="card-action">Open workspace <ArrowRight size={15} /></span></button>)}
        {!filtered.length && <p className="panel-empty col-span-full">No matching cases.</p>}
      </div>
      {showForm && <div className="modal-backdrop" role="presentation"><form className="case-modal" onSubmit={create} data-testid="new-case-form"><div className="panel-heading"><div><p className="kicker">NEW WORKSPACE</p><h3>Create a case</h3></div><button type="button" className="icon-btn" onClick={close} aria-label="Close"><X size={18} /></button></div><div className="form-grid"><label className="full">Corporate debtor name<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label><label>Internal reference<input value={form.internal_reference} onChange={e => setForm({ ...form, internal_reference: e.target.value })} /></label><label>CIN<input value={form.cin} onChange={e => setForm({ ...form, cin: e.target.value })} /></label><label>Process type<select value={form.process_type} onChange={e => setForm({ ...form, process_type: e.target.value })}><option>CIRP</option><option>Liquidation</option><option>Voluntary Liquidation</option><option>Pre-packaged Insolvency</option><option>NCLT Litigation</option></select></label><label>NCLT bench<input value={form.nclt_bench} onChange={e => setForm({ ...form, nclt_bench: e.target.value })} /></label><label className="full">Petition / application number<input value={form.petition_number} onChange={e => setForm({ ...form, petition_number: e.target.value })} /></label><label>Order date<input type="date" value={form.order_date} onChange={e => setForm({ ...form, order_date: e.target.value })} /></label><label>Commencement date<input type="date" value={form.commencement_date} onChange={e => setForm({ ...form, commencement_date: e.target.value })} /></label></div><div className="modal-actions"><button type="button" className="outline-button" onClick={close}>Cancel</button><button className="primary-button" disabled={busy}>{busy ? "Creating…" : "Create workspace"}</button></div></form></div>}
    </section>
  );
}
