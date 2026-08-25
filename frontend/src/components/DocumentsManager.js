import React, { useCallback, useEffect, useState } from "react";
import { Download, FileText, FileUp, WandSparkles } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";

export default function DocumentsManager({ caseRecord }) {
  const [documents, setDocuments] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [profile, setProfile] = useState({});
  const [templateId, setTemplateId] = useState("");
  const [values, setValues] = useState({});
  const [file, setFile] = useState(null);
  const selected = templates.find(item => item.id === templateId);
  const load = useCallback(async () => {
    try {
      const [docs, available, professional] = await Promise.all([api.get(`/cases/${caseRecord.id}/documents`), api.get("/templates"), api.get("/profile")]);
      setDocuments(docs.data); setTemplates(available.data); setProfile(professional.data);
    } catch (error) { toast.error(errorMessage(error, "Could not load case documents.")); }
  }, [caseRecord.id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setValues({ ...profile, ...(caseRecord.values || {}), cd_name: caseRecord.name }); }, [profile, caseRecord]);

  const generate = async event => {
    event.preventDefault();
    try {
      const { data } = await api.post("/documents", { case_id: caseRecord.id, template_id: templateId, values, tables: {}, notes: "" });
      toast.success("Document generated inside this case"); setTemplateId(""); await load();
      await downloadGenerated(data, "docx");
    } catch (error) { toast.error(errorMessage(error, "Could not generate the document.")); }
  };
  const downloadGenerated = async (record, format) => {
    try {
      const response = await api.get(`/documents/${record.id}/download/${format}`, { responseType: "blob" });
      const url = URL.createObjectURL(response.data); const link = document.createElement("a");
      link.href = url; link.download = `${record.name || record.template_name || "document"}.${format}`; link.click(); URL.revokeObjectURL(url);
    } catch (error) { toast.error(errorMessage(error, "Download failed.")); }
  };
  const upload = async event => {
    event.preventDefault(); if (!file) return;
    const body = new FormData(); body.append("file", file);
    try { await api.post(`/cases/${caseRecord.id}/documents/upload`, body); setFile(null); event.target.reset(); toast.success("Document uploaded"); load(); }
    catch (error) { toast.error(errorMessage(error, "Could not upload the document.")); }
  };
  const downloadUpload = async record => {
    try { const response = await api.get(`/cases/${caseRecord.id}/documents/${record.id}/file`, { responseType: "blob" }); const url = URL.createObjectURL(response.data); const link = document.createElement("a"); link.href = url; link.download = record.name; link.click(); URL.revokeObjectURL(url); }
    catch (error) { toast.error(errorMessage(error, "Download failed.")); }
  };

  return <div className="space-y-6"><div className="grid grid-cols-1 xl:grid-cols-2 gap-6"><section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">CASE-SPECIFIC GENERATION</p><h3>Generate document</h3></div><WandSparkles size={20} /></div><form onSubmit={generate} className="space-y-4"><label className="module-label">Document type<select required value={templateId} onChange={e => setTemplateId(e.target.value)}><option value="">Select an approved template</option>{templates.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{selected && <div className="form-grid">{selected.fields.map(field => <label className={field.key === "cd_address" ? "full" : ""} key={field.key}>{field.label}<input required={field.required} value={values[field.key] || ""} onChange={e => setValues({ ...values, [field.key]: e.target.value })} /></label>)}</div>}<button className="primary-button" disabled={!templateId}><FileText size={16} />Generate in {caseRecord.name}</button></form></section><section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">DOCUMENT MANAGEMENT</p><h3>Upload file</h3></div><FileUp size={20} /></div><form onSubmit={upload} className="upload-form"><input type="file" required accept=".pdf,.doc,.docx,.xls,.xlsx,.csv,.txt,.png,.jpg,.jpeg" onChange={e => setFile(e.target.files?.[0] || null)} /><small>Allowed case files up to 25 MB.</small><button className="outline-button" disabled={!file}>Upload to this case</button></form></section></div><section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">CASE REGISTER</p><h3>Document history</h3></div><span className="status-chip">{documents.length} records</span></div><div className="document-list">{documents.map(record => <div key={record.id} className="document-row"><FileText size={19} /><span><b>{record.name}</b><small>{record.category || record.source_type} · version {record.version}</small></span><button className="icon-btn" onClick={() => record.source_type === "generated" ? downloadGenerated(record, "docx") : downloadUpload(record)} aria-label="Download"><Download size={16} /></button></div>)}{!documents.length && <p className="panel-empty">No case documents yet.</p>}</div></section></div>;
}

