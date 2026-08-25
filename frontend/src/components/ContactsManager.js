import React, { useCallback, useEffect, useState } from "react";
import { Building2, Mail, Phone, Plus, UserRound } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";

const blank = { name: "", kind: "person", organization: "", role: "Stakeholder", category: "", email: "", phone: "", address: "", communication_preference: "Email", notes: "" };

export default function ContactsManager({ caseId }) {
  const [records, setRecords] = useState([]);
  const [form, setForm] = useState(blank);
  const [show, setShow] = useState(false);
  const load = useCallback(async () => {
    try { setRecords((await api.get(`/cases/${caseId}/contacts`)).data); }
    catch (error) { toast.error(errorMessage(error, "Could not load contacts.")); }
  }, [caseId]);
  useEffect(() => { load(); }, [load]);
  const create = async event => {
    event.preventDefault();
    try { const { data } = await api.post(`/cases/${caseId}/contacts`, form); setRecords(list => [...list, data]); setForm(blank); setShow(false); toast.success("Stakeholder added"); }
    catch (error) { toast.error(errorMessage(error, "Could not add the stakeholder.")); }
  };
  return <section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">CASE STAKEHOLDERS</p><h3>Contacts and roles</h3><p className="module-description">People and organizations can be reused firm-wide while retaining a separate role in this case.</p></div><button className="outline-button small" onClick={() => setShow(!show)}><Plus size={15} />Add</button></div>{show && <form className="module-create-form" onSubmit={create}><div className="form-grid"><label>Name<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label><label>Contact type<select value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })}><option value="person">Person</option><option value="organization">Organization</option></select></label><label>Organization<input value={form.organization} onChange={e => setForm({ ...form, organization: e.target.value })} /></label><label>Role in this case<input required value={form.role} onChange={e => setForm({ ...form, role: e.target.value })} /></label><label>Email<input type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label><label>Phone<input value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })} /></label><label className="full">Address<textarea value={form.address} onChange={e => setForm({ ...form, address: e.target.value })} /></label></div><div className="modal-actions"><button type="button" className="text-button" onClick={() => setShow(false)}>Cancel</button><button className="primary-button">Save stakeholder</button></div></form>}<div className="contact-grid">{records.map(record => <article className="contact-card" key={record.case_contact_id || record.id}>{record.kind === "organization" ? <Building2 size={20} /> : <UserRound size={20} />}<div><h4>{record.name}</h4><p>{record.role}{record.organization ? ` · ${record.organization}` : ""}</p>{record.email && <span><Mail size={13} />{record.email}</span>}{record.phone && <span><Phone size={13} />{record.phone}</span>}</div></article>)}{!records.length && <p className="panel-empty">No stakeholders recorded.</p>}</div></section>;
}

