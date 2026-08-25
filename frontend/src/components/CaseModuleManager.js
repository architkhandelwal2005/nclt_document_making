import React, { useCallback, useEffect, useState } from "react";
import { Archive, Check, Plus, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";

const today = () => new Date().toISOString().slice(0, 10);
const nowLocal = () => {
  const date = new Date();
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
};

function initialValue(field) {
  if (field.default !== undefined) return field.default;
  if (field.type === "date" && field.today) return today();
  if (field.type === "datetime-local" && field.now) return nowLocal();
  if (field.type === "number") return 0;
  if (field.type === "checkbox") return false;
  return "";
}

export default function CaseModuleManager({ caseId, module, title, description, fields, columns, allowCreate = true }) {
  const makeBlank = useCallback(() => Object.fromEntries(fields.map(field => [field.key, initialValue(field)])), [fields]);
  const [records, setRecords] = useState([]);
  const [form, setForm] = useState(makeBlank);
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try { setRecords((await api.get(`/cases/${caseId}/${module}`)).data); }
    catch (error) { toast.error(errorMessage(error, `Could not load ${title.toLowerCase()}.`)); }
    finally { setLoading(false); }
  }, [caseId, module, title]);
  useEffect(() => { load(); }, [load]);

  const create = async event => {
    event.preventDefault(); setBusy(true);
    try {
      const { data } = await api.post(`/cases/${caseId}/${module}`, form);
      setRecords(list => [data, ...list]); setForm(makeBlank()); setShowForm(false); toast.success(`${title} record created`);
    } catch (error) { toast.error(errorMessage(error, `Could not create the ${title.toLowerCase()} record.`)); }
    finally { setBusy(false); }
  };
  const update = async (record, change) => {
    try {
      const { data } = await api.put(`/cases/${caseId}/${module}/${record.id}`, change);
      setRecords(list => list.map(item => item.id === record.id ? data : item)); toast.success("Record updated");
    } catch (error) { toast.error(errorMessage(error, "Could not update the record.")); }
  };
  const archive = async record => {
    if (!window.confirm(`Archive this ${title.toLowerCase()} record? It will remain in the audit history.`)) return;
    try { await api.delete(`/cases/${caseId}/${module}/${record.id}`); setRecords(list => list.filter(item => item.id !== record.id)); toast.success("Record archived"); }
    catch (error) { toast.error(errorMessage(error, "Could not archive the record.")); }
  };

  return <section className="casefile-panel module-manager" data-testid={`module-${module}`}>
    <div className="panel-heading"><div><p className="kicker">{module.replaceAll("-", " ").toUpperCase()}</p><h3>{title}</h3>{description && <p className="module-description">{description}</p>}</div><div className="flex gap-2"><button className="icon-btn" onClick={load} aria-label={`Refresh ${title}`}><RefreshCw size={16} /></button>{allowCreate && <button className="outline-button small" onClick={() => setShowForm(value => !value)}><Plus size={15} />Add</button>}</div></div>
    {showForm && <form className="module-create-form" onSubmit={create}><div className="form-grid">{fields.map(field => <label className={field.full ? "full" : ""} key={field.key}>{field.label}{field.type === "textarea" ? <textarea required={field.required} value={form[field.key]} onChange={e => setForm({ ...form, [field.key]: e.target.value })} /> : field.type === "select" ? <select required={field.required} value={form[field.key]} onChange={e => setForm({ ...form, [field.key]: e.target.value })}>{field.options.map(option => <option value={option.value ?? option} key={option.value ?? option}>{(option.label ?? option) || "Select"}</option>)}</select> : field.type === "checkbox" ? <input type="checkbox" checked={form[field.key]} onChange={e => setForm({ ...form, [field.key]: e.target.checked })} /> : <input type={field.type || "text"} step={field.step} required={field.required} value={form[field.key]} onChange={e => setForm({ ...form, [field.key]: e.target.value })} />}</label>)}</div><div className="modal-actions"><button type="button" className="text-button" onClick={() => setShowForm(false)}><X size={15} />Cancel</button><button className="primary-button" disabled={busy}><Check size={15} />{busy ? "Saving…" : "Save record"}</button></div></form>}
    <div className="module-table-wrap"><table className="module-table"><thead><tr>{columns.map(column => <th key={column.key}>{column.label}</th>)}<th>Status</th><th aria-label="Actions" /></tr></thead><tbody>{records.map(record => <tr key={record.id}>{columns.map(column => <td key={column.key}>{column.format ? column.format(record[column.key], record) : record[column.key] || "—"}</td>)}<td>{record.status && fields.some(field => field.key === "status") ? <select className="inline-status" value={record.status} onChange={e => update(record, { status: e.target.value, ...(e.target.value === "completed" ? { completed_at: new Date().toISOString() } : {}) })}>{fields.find(field => field.key === "status")?.options?.map(option => <option value={option.value ?? option} key={option.value ?? option}>{option.label ?? option}</option>)}</select> : <span className="status-chip">{record.status || "recorded"}</span>}</td><td><button className="icon-btn" onClick={() => archive(record)} aria-label="Archive record"><Archive size={15} /></button></td></tr>)}{!records.length && !loading && <tr><td colSpan={columns.length + 2} className="panel-empty">No records have been added.</td></tr>}{loading && <tr><td colSpan={columns.length + 2} className="panel-empty">Loading…</td></tr>}</tbody></table></div>
  </section>;
}

export const field = (key, label, type = "text", extras = {}) => ({ key, label, type, ...extras });
