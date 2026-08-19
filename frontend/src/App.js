import { useEffect, useMemo, useState, useCallback } from "react";
import "@/App.css";
import axios from "axios";
import {
  FileText, LayoutDashboard, Settings, Clock3, Plus, Download,
  ArrowRight, Search, CheckCircle2, ChevronLeft, LogOut, Upload,
  Trash2, PlusCircle, Lock, Save, BookmarkPlus, Layers, FileUp,
  X,
} from "lucide-react";
import { toast, Toaster } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const TOKEN_KEY = "casefile.token";

axios.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token && config.url && config.url.startsWith(API)) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

function formatDetail(detail) {
  if (detail == null) return "Something went wrong. Please try again.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(e => (e && e.msg) || JSON.stringify(e)).join(" ");
  if (detail && detail.msg) return detail.msg;
  return String(detail);
}

function Login({ onSuccess }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const { data } = await axios.post(`${API}/auth/login`, { email, password });
      localStorage.setItem(TOKEN_KEY, data.access_token);
      onSuccess(data.user);
      toast.success(`Welcome back, ${data.user.name}`);
    } catch (err) {
      toast.error(formatDetail(err.response?.data?.detail) || "Login failed");
    } finally { setBusy(false); }
  };
  return (
    <div className="login-shell">
      <div className="login-brand">
        <span className="brand-mark login-mark">N</span>
        <p className="eyebrow">CASEFILE / NCLT WORKSPACE</p>
        <h1>Sign in to continue<br /><em>drafting your matter.</em></h1>
        <p className="login-copy">A private workspace for chartered accountants working NCLT and insolvency assignments. Fill fixed templates, export Word or PDF.</p>
      </div>
      <form className="login-card" onSubmit={submit} data-testid="login-form">
        <p className="kicker">SIGN IN</p>
        <h2>Casefile</h2>
        <label>Email
          <input data-testid="login-email-input" type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required placeholder="you@firm.co" />
        </label>
        <label>Password
          <input data-testid="login-password-input" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required placeholder="Enter your password" />
        </label>
        <button className="primary-button login-submit" data-testid="login-submit-button" disabled={busy} type="submit">
          <Lock size={16} /> {busy ? "Signing in..." : "Sign in"}
        </button>
        <small className="login-hint">Contact your administrator if you need access.</small>
      </form>
    </div>
  );
}

function App() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  const [view, setView] = useState("workspace");
  const [templates, setTemplates] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [drafts, setDrafts] = useState([]);
  const [selected, setSelected] = useState(null);
  const [step, setStep] = useState(1);
  const [values, setValues] = useState({});
  const [tables, setTables] = useState({});
  const [notes, setNotes] = useState("");
  const [draftId, setDraftId] = useState(null);
  const [draftName, setDraftName] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) { setChecking(false); return; }
    axios.get(`${API}/auth/me`).then(({ data }) => setUser(data))
      .catch(() => localStorage.removeItem(TOKEN_KEY))
      .finally(() => setChecking(false));
  }, []);

  const loadWorkspace = useCallback(() => {
    Promise.all([axios.get(`${API}/templates`), axios.get(`${API}/documents`), axios.get(`${API}/drafts`)])
      .then(([t, d, dr]) => { setTemplates(t.data); setDocuments(d.data); setDrafts(dr.data); })
      .catch(() => toast.error("Could not connect to the workspace"));
  }, []);

  useEffect(() => { if (user) loadWorkspace(); }, [user, loadWorkspace]);

  const filtered = useMemo(() => templates.filter(t => `${t.name} ${t.category}`.toLowerCase().includes(query.toLowerCase())), [templates, query]);

  const start = (template) => {
    const initialTables = {};
    (template.table_inputs || []).forEach(t => {
      initialTables[t.key] = [t.columns, ...Array.from({ length: 2 }, () => t.columns.map(() => ""))];
    });
    setSelected(template); setValues({}); setTables(initialTables); setNotes("");
    setDraftId(null); setDraftName(`${template.name} — ${new Date().toLocaleDateString()}`);
    setStep(2); setView("new");
  };

  const openDraft = async (draft) => {
    const template = templates.find(t => t.id === draft.template_id);
    if (!template) { toast.error("Template for this draft is no longer available"); return; }
    // Merge template-defined tables with saved rows so column headers persist
    const mergedTables = {};
    (template.table_inputs || []).forEach(t => {
      mergedTables[t.key] = draft.tables?.[t.key]?.length ? draft.tables[t.key] : [t.columns, ...Array.from({ length: 2 }, () => t.columns.map(() => ""))];
    });
    setSelected(template);
    setValues(draft.values || {});
    setTables(mergedTables);
    setNotes(draft.notes || "");
    setDraftId(draft.id);
    setDraftName(draft.name);
    setStep(2); setView("new");
  };

  const saveDraft = async () => {
    try {
      const cleanTables = {};
      Object.entries(tables).forEach(([k, rows]) => {
        const nonEmpty = rows.filter((row, idx) => idx === 0 || row.some(c => String(c || "").trim()));
        if (nonEmpty.length > 1) cleanTables[k] = nonEmpty;
      });
      const { data } = await axios.post(`${API}/drafts`, { id: draftId, name: draftName || `Draft ${new Date().toLocaleDateString()}`, template_id: selected.id, values, tables: cleanTables, notes });
      setDraftId(data.id);
      setDrafts([data, ...drafts.filter(d => d.id !== data.id)]);
      toast.success("Draft saved");
    } catch (e) {
      toast.error(formatDetail(e.response?.data?.detail) || "Could not save draft");
    }
  };

  const deleteDraft = async (id) => {
    try {
      await axios.delete(`${API}/drafts/${id}`);
      setDrafts(drafts.filter(d => d.id !== id));
      toast.success("Draft removed");
    } catch (e) {
      toast.error("Could not delete draft");
    }
  };

  const deleteTemplate = async (id) => {
    try {
      await axios.delete(`${API}/templates/${id}`);
      setTemplates(templates.filter(t => t.id !== id));
      toast.success("Template removed");
    } catch (e) {
      toast.error(formatDetail(e.response?.data?.detail) || "Could not delete template");
    }
  };

  const generate = async () => {
    try {
      const cleanTables = {};
      Object.entries(tables).forEach(([k, rows]) => {
        const nonEmpty = rows.filter((row, idx) => idx === 0 || row.some(c => String(c || "").trim()));
        if (nonEmpty.length > 1) cleanTables[k] = nonEmpty;
      });
      const { data } = await axios.post(`${API}/documents`, { template_id: selected.id, values, tables: cleanTables, notes });
      setDocuments([data, ...documents]);
      setSelected({ ...selected, generated: data });
      setStep(3);
      toast.success("Document ready to export");
      // Auto-remove draft if it was opened
      if (draftId) { axios.delete(`${API}/drafts/${draftId}`).catch(() => {}); setDrafts(drafts.filter(d => d.id !== draftId)); setDraftId(null); }
    } catch (e) {
      toast.error(formatDetail(e.response?.data?.detail) || "Please complete the required fields");
    }
  };

  const download = (format) => {
    const token = localStorage.getItem(TOKEN_KEY);
    window.open(`${API}/documents/${selected.generated.id}/export/${format}?token=${encodeURIComponent(token)}`, "_blank");
  };

  const logout = () => {
    localStorage.removeItem(TOKEN_KEY);
    setUser(null); setView("workspace"); setTemplates([]); setDocuments([]); setDrafts([]);
    toast.success("Signed out");
  };

  if (checking) return <div className="app-loading" data-testid="app-loading">Loading workspace...</div>;
  if (!user) return (<><Login onSuccess={setUser} /><Toaster position="bottom-right" /></>);

  const nav = [
    { id: "workspace", label: "Workspace", icon: LayoutDashboard },
    { id: "templates", label: "Templates", icon: FileText },
    { id: "drafts", label: "Drafts", icon: BookmarkPlus },
    { id: "recent", label: "Recent documents", icon: Clock3 },
    { id: "upload", label: "Manage templates", icon: Layers },
    { id: "settings", label: "Settings", icon: Settings },
  ];

  const initials = user.name.split(" ").map(p => p[0]).slice(0, 2).join("").toUpperCase();
  const title = { new: "New document", templates: "Template library", drafts: "Saved drafts", recent: "Recent documents", upload: "Manage templates", settings: "Settings" }[view] || "Workspace";

  return (
    <div className="app-shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark">N</span>
          <div><strong>Casefile</strong><small>NCLT WORKSPACE</small></div>
        </div>
        <div className="rail-rule" />
        <nav>
          {nav.map(({ id, label, icon: Icon }) => (
            <button key={id} data-testid={`nav-${id}-button`} className={view === id ? "nav-item active" : "nav-item"} onClick={() => setView(id)}>
              <Icon size={17} />{label}
            </button>
          ))}
        </nav>
        <button className="new-button" data-testid="new-document-button" onClick={() => { setView("templates"); setStep(1); }}>
          <Plus size={17} />New document
        </button>
        <div className="rail-footer">
          <span className="avatar">{initials}</span>
          <div><b>{user.name}</b><small>{user.role.toUpperCase()}</small></div>
          <button className="logout-btn" data-testid="logout-button" onClick={logout} title="Sign out"><LogOut size={15} /></button>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div>
            <p className="eyebrow">DOCUMENT OPERATIONS / 01</p>
            <h1 data-testid="page-title">{title}</h1>
          </div>
          <div className="top-status"><span className="status-dot" />All changes saved</div>
        </header>
        {view === "new" ? (
          <Generator selected={selected} step={step} values={values} setValues={setValues} tables={tables} setTables={setTables} notes={notes} setNotes={setNotes} draftName={draftName} setDraftName={setDraftName} draftId={draftId} onGenerate={generate} onBack={() => setView("templates")} onDownload={download} onSaveDraft={saveDraft} />
        ) : view === "drafts" ? (
          <DraftsView drafts={drafts} onOpen={openDraft} onDelete={deleteDraft} onNew={() => setView("templates")} />
        ) : view === "upload" ? (
          <TemplateUploader onSaved={(t) => { setTemplates([...templates, t]); toast.success(`${t.name} added`); }} customTemplates={templates.filter(t => t.source === "custom")} onDelete={deleteTemplate} />
        ) : (
          <Workspace view={view} templates={filtered} documents={documents} drafts={drafts} query={query} setQuery={setQuery} onStart={start} onOpenDraft={openDraft} onNew={() => setView("templates")} setView={setView} user={user} onDeleteTemplate={deleteTemplate} />
        )}
      </main>
      <Toaster position="bottom-right" />
    </div>
  );
}

function Workspace({ view, templates, documents, drafts, query, setQuery, onStart, onOpenDraft, onNew, setView, user, onDeleteTemplate }) {
  return (
    <section className="workspace">
      <div className="workspace-intro">
        <div>
          <p className="kicker">{view === "workspace" ? `WELCOME, ${user.name.toUpperCase()}` : "YOUR LIBRARY"}</p>
          <h2>
            {view === "workspace" ? "Keep your casework moving." :
              view === "templates" ? "Start from a trusted format." :
              view === "recent" ? "Your generated documents." : "Workspace preferences."}
          </h2>
          <p className="intro-copy">
            {view === "workspace" ? "Turn familiar NCLT and insolvency formats into finished documents in minutes." :
              view === "settings" ? "Manage the signed-in account and reference credentials." :
              "Fixed templates, clear inputs, and export-ready files."}
          </p>
        </div>
        {view !== "settings" && (
          <button className="primary-button" data-testid="workspace-new-document-button" onClick={onNew}>
            <Plus size={17} />New document <ArrowRight size={16} />
          </button>
        )}
      </div>
      {view === "settings" ? (
        <div className="settings-panel" data-testid="settings-panel">
          <p className="kicker">ACCOUNT</p>
          <h3>Workspace settings</h3>
          <dl className="settings-list">
            <div><dt>Signed in as</dt><dd>{user.name}</dd></div>
            <div><dt>Email</dt><dd>{user.email}</dd></div>
            <div><dt>Role</dt><dd>{user.role}</dd></div>
          </dl>
          <p className="settings-hint">Templates are managed from the Manage templates screen.</p>
        </div>
      ) : view === "recent" ? <Recent documents={documents} />
      : (
        <>
          <div className="section-heading">
            <div>
              <p className="kicker">{view === "workspace" ? "QUICK START" : "AVAILABLE FORMATS"}</p>
              <h3>Templates</h3>
            </div>
            <label className="search">
              <Search size={16} />
              <input data-testid="template-search-input" value={query} onChange={e => setQuery(e.target.value)} placeholder="Search templates" />
            </label>
          </div>
          <div className="template-grid">
            {templates.map(t => <TemplateCard key={t.id} template={t} onStart={onStart} onDelete={onDeleteTemplate} />)}
          </div>
          {view === "workspace" && drafts.length > 0 && (
            <div className="recent-strip">
              <div className="section-heading">
                <div><p className="kicker">IN PROGRESS</p><h3>Saved drafts</h3></div>
                <button className="text-button" data-testid="view-drafts-button" onClick={() => setView("drafts")}>View all <ArrowRight size={15} /></button>
              </div>
              <DraftsList drafts={drafts.slice(0, 3)} onOpen={onOpenDraft} />
            </div>
          )}
          {view === "workspace" && (
            <div className="recent-strip">
              <div className="section-heading">
                <div><p className="kicker">ACTIVITY</p><h3>Recent documents</h3></div>
                <button className="text-button" data-testid="view-recent-button" onClick={() => setView("recent")}>View all <ArrowRight size={15} /></button>
              </div>
              <Recent documents={documents.slice(0, 3)} />
            </div>
          )}
        </>
      )}
    </section>
  );
}

function TemplateCard({ template, onStart, onDelete }) {
  const isCustom = template.source === "custom";
  return (
    <article className="template-card">
      <div className="doc-thumb">
        <span>{isCustom ? "CUSTOM" : "CASEFILE"}</span>
        <strong>{template.name.split(" ").slice(0, 2).join(" ")}</strong>
        <i>{template.category}</i>
      </div>
      <div className="template-info">
        <div className="template-info-head">
          <span className="category-tag">{template.category}</span>
          {isCustom && onDelete && (
            <button className="icon-btn" data-testid={`delete-template-${template.id}-button`} onClick={() => { if (window.confirm(`Remove ${template.name}?`)) onDelete(template.id); }} aria-label="Remove template" title="Remove custom template">
              <Trash2 size={13} />
            </button>
          )}
        </div>
        <h4>{template.name}</h4>
        <p>{template.description || "Custom template you uploaded."}</p>
        <button className="outline-button" data-testid={`use-template-${template.id}-button`} onClick={() => onStart(template)}>
          Use template <ArrowRight size={15} />
        </button>
      </div>
    </article>
  );
}

function Recent({ documents }) {
  return (
    <div className="recent-list">
      {documents.length ? documents.map(d => (
        <div className="recent-row" key={d.id} data-testid={`recent-document-${d.id}`}>
          <div className="file-icon"><FileText size={18} /></div>
          <div><b>{d.company_name}</b><span>{d.template_name} · {new Date(d.created_at).toLocaleDateString()}</span></div>
          <em><CheckCircle2 size={14} />{d.status}</em>
        </div>
      )) : (
        <div className="empty-state">
          <FileText size={24} />
          <b>No generated documents yet</b>
          <span>Choose a template to create your first case document.</span>
        </div>
      )}
    </div>
  );
}

function DraftsList({ drafts, onOpen, onDelete }) {
  return (
    <div className="recent-list">
      {drafts.map(d => (
        <div className="recent-row" key={d.id} data-testid={`draft-row-${d.id}`}>
          <div className="file-icon"><BookmarkPlus size={18} /></div>
          <div><b>{d.name}</b><span>{d.template_name} · updated {new Date(d.updated_at).toLocaleDateString()}</span></div>
          <button className="outline-button small" data-testid={`open-draft-${d.id}`} onClick={() => onOpen(d)}>Resume <ArrowRight size={14} /></button>
          {onDelete && (
            <button className="icon-btn" data-testid={`delete-draft-${d.id}`} onClick={() => { if (window.confirm(`Delete ${d.name}?`)) onDelete(d.id); }} aria-label="Delete draft">
              <Trash2 size={14} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function DraftsView({ drafts, onOpen, onDelete, onNew }) {
  return (
    <section className="workspace">
      <div className="workspace-intro">
        <div>
          <p className="kicker">SAVED DRAFTS</p>
          <h2>Pick up where you left off.</h2>
          <p className="intro-copy">Each draft keeps the case details, uploaded tables and notes. Resume any time — nothing is lost.</p>
        </div>
        <button className="primary-button" data-testid="drafts-new-button" onClick={onNew}><Plus size={17} />New draft <ArrowRight size={16} /></button>
      </div>
      {drafts.length ? (
        <DraftsList drafts={drafts} onOpen={onOpen} onDelete={onDelete} />
      ) : (
        <div className="empty-state" data-testid="drafts-empty">
          <BookmarkPlus size={24} />
          <b>No drafts saved yet</b>
          <span>Start a document and use “Save draft” to keep your progress.</span>
        </div>
      )}
    </section>
  );
}

function TableEditor({ table, rows, onChange }) {
  const columns = table.columns;
  const dataRows = rows && rows.length > 0 ? rows.slice(1) : [];

  const updateCell = (rowIdx, colIdx, value) => {
    const next = [columns, ...dataRows.map(r => [...r])];
    while (next[rowIdx + 1].length < columns.length) next[rowIdx + 1].push("");
    next[rowIdx + 1][colIdx] = value;
    onChange(next);
  };
  const addRow = () => onChange([columns, ...dataRows.map(r => [...r]), columns.map(() => "")]);
  const removeRow = (idx) => onChange([columns, ...dataRows.filter((_, i) => i !== idx)]);

  const uploadCsv = (file) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const parsed = e.target.result.trim().split(/\r?\n/).filter(Boolean).map(row => row.split(",").map(c => c.trim()));
      if (!parsed.length) return;
      const [head, ...rest] = parsed;
      const useHead = head.length === columns.length ? head : null;
      const finalRows = useHead ? [useHead, ...rest] : [columns, ...parsed];
      onChange(finalRows);
      toast.success(`${finalRows.length - 1} rows loaded into ${table.label.toLowerCase()}`);
    };
    reader.readAsText(file);
  };

  return (
    <div className="table-editor" data-testid={`table-editor-${table.key}`}>
      <div className="table-editor-head">
        <div><h5>{table.label}</h5><span>{dataRows.length} row{dataRows.length === 1 ? "" : "s"}</span></div>
        <label className="csv-upload"><Upload size={13} /><span>Import CSV</span>
          <input data-testid={`upload-${table.key}-input`} type="file" accept=".csv,text/csv" onChange={e => e.target.files[0] && uploadCsv(e.target.files[0])} />
        </label>
      </div>
      <div className="table-scroll">
        <table>
          <thead><tr>{columns.map((c, i) => <th key={i}>{c}</th>)}<th aria-label="actions" /></tr></thead>
          <tbody>
            {dataRows.length === 0 ? (
              <tr className="empty-row"><td colSpan={columns.length + 1}>No rows yet — add manually or import CSV</td></tr>
            ) : dataRows.map((row, rIdx) => (
              <tr key={rIdx}>
                {columns.map((_, cIdx) => (
                  <td key={cIdx}>
                    <input data-testid={`table-${table.key}-row-${rIdx}-col-${cIdx}`} value={row[cIdx] || ""} onChange={e => updateCell(rIdx, cIdx, e.target.value)} placeholder={columns[cIdx]} />
                  </td>
                ))}
                <td className="row-actions">
                  <button type="button" data-testid={`remove-row-${table.key}-${rIdx}`} onClick={() => removeRow(rIdx)} aria-label="Remove row"><Trash2 size={14} /></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button type="button" className="add-row-btn" data-testid={`add-row-${table.key}`} onClick={addRow}><PlusCircle size={14} /> Add row</button>
    </div>
  );
}

function Generator({ selected, step, values, setValues, tables, setTables, notes, setNotes, draftName, setDraftName, draftId, onGenerate, onBack, onDownload, onSaveDraft }) {
  const groups = [...new Set(selected.fields.map(f => f.section))];
  const missing = selected.fields.filter(f => f.required && !values[f.key]);
  return (
    <section className="generator">
      <div className="stepper">
        {[[1, "Template"], [2, "Case details"], [3, "Review & export"]].map(([n, label]) => (
          <div className={step === n ? "step active" : step > n ? "step done" : "step"} key={n}>
            <span>{step > n ? "✓" : `0${n}`}</span>{label}
          </div>
        ))}
      </div>
      <div className="generator-head">
        <button className="back-button" data-testid="generator-back-button" onClick={onBack}><ChevronLeft size={18} />Templates</button>
        <div><p className="kicker">{selected.category}{draftId ? " · RESUMING DRAFT" : ""}</p><h2>{selected.name}</h2></div>
      </div>
      {step === 2 && (
        <div className="form-workspace">
          <form className="case-form" onSubmit={(e) => { e.preventDefault(); onGenerate(); }}>
            <fieldset className="draft-fieldset">
              <legend>Draft name</legend>
              <label className="draft-name-label">Matter reference
                <input data-testid="draft-name-input" value={draftName} onChange={e => setDraftName(e.target.value)} placeholder="e.g. Acme Ltd — 1st CoC" />
              </label>
            </fieldset>
            {groups.map(group => (
              <fieldset key={group}>
                <legend>{group}</legend>
                {selected.fields.filter(f => f.section === group).map(field => (
                  <label key={field.key} data-testid={`field-${field.key}-label`}>
                    {field.label}{field.required && <span className="required">Required</span>}
                    <input data-testid={`field-${field.key}-input`} required={field.required} value={values[field.key] || ""} placeholder={field.placeholder} onChange={e => setValues({ ...values, [field.key]: e.target.value })} />
                  </label>
                ))}
              </fieldset>
            ))}
            {selected.table_inputs?.length > 0 && (
              <fieldset className="table-fieldset">
                <legend>Schedules and lists</legend>
                <p className="table-hint">Fill inline or import a CSV. First-row headers must match the columns shown.</p>
                {selected.table_inputs.map(table => (
                  <TableEditor key={table.key} table={table} rows={tables[table.key]} onChange={next => setTables({ ...tables, [table.key]: next })} />
                ))}
              </fieldset>
            )}
            <label className="notes-label">Additional notes <span>Optional</span>
              <textarea data-testid="additional-notes-input" value={notes} onChange={e => setNotes(e.target.value)} placeholder="Add any matter-specific notes to include at the end..." />
            </label>
            <div className="form-footer">
              <span>{missing.length ? `${missing.length} required ${missing.length === 1 ? "field" : "fields"} remaining` : "All required fields complete"}</span>
              <div className="form-footer-actions">
                <button type="button" className="outline-button" data-testid="save-draft-button" onClick={onSaveDraft}><Save size={15} /> Save draft</button>
                <button className="primary-button" data-testid="generate-document-button" type="submit">Generate document <ArrowRight size={16} /></button>
              </div>
            </div>
          </form>
          <Preview selected={selected} values={values} />
        </div>
      )}
      {step === 3 && (
        <div className="export-state" data-testid="export-state">
          <div className="success-mark"><CheckCircle2 size={32} /></div>
          <p className="kicker">READY TO FILE</p>
          <h2>{selected.generated.company_name}</h2>
          <p>Your {selected.generated.template_name} is ready. Download an editable Word copy or a print-ready PDF.</p>
          <div className="export-actions">
            <button className="primary-button" data-testid="download-word-button" onClick={() => onDownload("docx")}><Download size={17} />Download Word</button>
            <button className="outline-button" data-testid="download-pdf-button" onClick={() => onDownload("pdf")}><Download size={17} />Download PDF</button>
          </div>
          <button className="text-button" data-testid="create-another-button" onClick={onBack}>Create another document <ArrowRight size={15} /></button>
        </div>
      )}
    </section>
  );
}

function Preview({ selected, values }) {
  return (
    <div className="preview-wrap">
      <div className="preview-label">
        <span>LIVE PREVIEW</span>
        <small>{selected.fields.filter(f => values[f.key]).length}/{selected.fields.length} fields</small>
      </div>
      <div className="paper-preview" data-testid="document-preview">
        <p className="paper-eyebrow">BEFORE THE NATIONAL COMPANY LAW TRIBUNAL</p>
        <h3>{selected.name.toUpperCase()}</h3>
        <hr />
        {selected.fields.slice(0, 5).map(f => (
          <p key={f.key}>
            <b>{f.label}</b>
            <span className={values[f.key] ? "filled" : "blank"}>{values[f.key] || "Awaiting input"}</span>
          </p>
        ))}
        <div className="paper-line" />
        <small>Preview updates as you complete the case details.</small>
      </div>
    </div>
  );
}

function TemplateUploader({ onSaved, customTemplates, onDelete }) {
  const [file, setFile] = useState(null);
  const [inspected, setInspected] = useState(null);
  const [meta, setMeta] = useState({ name: "", category: "Custom", description: "" });
  const [busy, setBusy] = useState(false);

  const inspect = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const { data } = await axios.post(`${API}/templates/inspect`, form);
      setInspected(data);
      setMeta(m => ({ ...m, name: m.name || file.name.replace(/\.docx$/i, "") }));
      toast.success(`Detected ${data.detected_fields.length} field${data.detected_fields.length === 1 ? "" : "s"} and ${data.detected_tables.length} table${data.detected_tables.length === 1 ? "" : "s"}`);
    } catch (e) {
      toast.error(formatDetail(e.response?.data?.detail) || "Could not read the DOCX");
    } finally { setBusy(false); }
  };

  const updateField = (idx, patch) => {
    const next = [...inspected.detected_fields];
    next[idx] = { ...next[idx], ...patch };
    setInspected({ ...inspected, detected_fields: next });
  };
  const updateTable = (idx, patch) => {
    const next = [...inspected.detected_tables];
    next[idx] = { ...next[idx], ...patch };
    setInspected({ ...inspected, detected_tables: next });
  };

  const save = async () => {
    if (!inspected || !meta.name.trim()) { toast.error("Give the template a name first"); return; }
    setBusy(true);
    try {
      const body = {
        upload_id: inspected.upload_id,
        name: meta.name,
        category: meta.category,
        description: meta.description,
        fields: inspected.detected_fields,
        table_inputs: inspected.detected_tables.map(t => ({ ...t, columns: Array.isArray(t.columns) ? t.columns : String(t.columns).split(",").map(s => s.trim()).filter(Boolean) })),
      };
      const { data } = await axios.post(`${API}/templates`, body);
      onSaved(data);
      setFile(null); setInspected(null); setMeta({ name: "", category: "Custom", description: "" });
    } catch (e) {
      toast.error(formatDetail(e.response?.data?.detail) || "Could not save template");
    } finally { setBusy(false); }
  };

  return (
    <section className="workspace">
      <div className="workspace-intro">
        <div>
          <p className="kicker">TEMPLATE LIBRARY</p>
          <h2>Add a new template.</h2>
          <p className="intro-copy">Upload any DOCX that contains <code>{"{{placeholder}}"}</code> tokens. Casefile detects them, lets you label each one and adds the template to your library — no code change needed.</p>
        </div>
      </div>
      <div className="uploader-grid">
        <div className="uploader-panel" data-testid="uploader-panel">
          <p className="kicker">STEP 1</p>
          <h3>Choose DOCX</h3>
          <label className="file-drop">
            <FileUp size={22} />
            <span>{file ? file.name : "Drop a .docx or click to browse"}</span>
            <input data-testid="uploader-file-input" type="file" accept=".docx" onChange={e => { setFile(e.target.files?.[0] || null); setInspected(null); }} />
          </label>
          <button className="primary-button" data-testid="uploader-inspect-button" disabled={!file || busy} onClick={inspect}>
            <Upload size={15} /> {busy && !inspected ? "Reading..." : "Detect placeholders"}
          </button>
          <hr />
          <p className="kicker">CUSTOM TEMPLATES</p>
          {customTemplates.length ? (
            <ul className="custom-list">
              {customTemplates.map(t => (
                <li key={t.id} data-testid={`custom-template-${t.id}`}>
                  <div><b>{t.name}</b><span>{t.category} · {t.fields.length} fields</span></div>
                  <button className="icon-btn" onClick={() => { if (window.confirm(`Remove ${t.name}?`)) onDelete(t.id); }} aria-label="Delete"><Trash2 size={13} /></button>
                </li>
              ))}
            </ul>
          ) : <p className="muted-hint">Nothing here yet. Upload your first template on the right.</p>}
        </div>
        {inspected && (
          <div className="uploader-panel" data-testid="uploader-configure">
            <p className="kicker">STEP 2</p>
            <h3>Configure fields</h3>
            <div className="uploader-meta">
              <label>Template name
                <input data-testid="uploader-name-input" value={meta.name} onChange={e => setMeta({ ...meta, name: e.target.value })} placeholder="e.g. IA 87 draft" />
              </label>
              <label>Category
                <input data-testid="uploader-category-input" value={meta.category} onChange={e => setMeta({ ...meta, category: e.target.value })} placeholder="e.g. NCLT filing" />
              </label>
              <label className="full">Description
                <input data-testid="uploader-description-input" value={meta.description} onChange={e => setMeta({ ...meta, description: e.target.value })} placeholder="Short line shown on the template card" />
              </label>
            </div>
            <h4>Detected fields</h4>
            <div className="field-list">
              {inspected.detected_fields.map((f, i) => (
                <div className="field-row" key={f.key} data-testid={`detected-field-${f.key}`}>
                  <code>{`{{${f.key}}}`}</code>
                  <input aria-label="Label" value={f.label} onChange={e => updateField(i, { label: e.target.value })} placeholder="Label shown to user" />
                  <input aria-label="Section" value={f.section} onChange={e => updateField(i, { section: e.target.value })} placeholder="Section" />
                  <label className="required-check">
                    <input type="checkbox" checked={f.required} onChange={e => updateField(i, { required: e.target.checked })} /> required
                  </label>
                </div>
              ))}
            </div>
            {inspected.detected_tables.length > 0 && (
              <>
                <h4>Detected tables</h4>
                <div className="field-list">
                  {inspected.detected_tables.map((t, i) => (
                    <div className="field-row wide" key={t.key} data-testid={`detected-table-${t.key}`}>
                      <code>{`{{${t.key}}}`}</code>
                      <input aria-label="Table label" value={t.label} onChange={e => updateTable(i, { label: e.target.value })} placeholder="Table label" />
                      <input aria-label="Columns" value={Array.isArray(t.columns) ? t.columns.join(", ") : t.columns} onChange={e => updateTable(i, { columns: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} placeholder="Comma-separated column headers" />
                    </div>
                  ))}
                </div>
              </>
            )}
            <div className="uploader-footer">
              <button className="text-button" data-testid="uploader-cancel-button" onClick={() => { setInspected(null); setFile(null); }}><X size={14} /> Cancel</button>
              <button className="primary-button" data-testid="uploader-save-button" onClick={save} disabled={busy}><Save size={15} /> {busy ? "Saving..." : "Save template"}</button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

export default App;
