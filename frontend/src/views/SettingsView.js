import React, { useEffect, useState } from "react";
import { Download, Plus, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";

const blank = { name: "", email: "", role: "associate", password: "" };

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export default function SettingsView({ user }) {
  const [status, setStatus] = useState({});
  const [users, setUsers] = useState([]);
  const [assignments, setAssignments] = useState([]);
  const [form, setForm] = useState(blank);
  const [show, setShow] = useState(false);
  const [databaseRestoreFile, setDatabaseRestoreFile] = useState(null);
  const [completeRestoreFile, setCompleteRestoreFile] = useState(null);

  const load = () => Promise.all([
    api.get("/admin/security-status"),
    api.get("/admin/users"),
    api.get("/admin/case-assignments"),
  ]).then(([security, officeUsers, caseAssignments]) => {
    setStatus(security.data);
    setUsers(officeUsers.data);
    setAssignments(caseAssignments.data);
  }).catch(error => toast.error(errorMessage(error, "Administrator access is required.")));

  useEffect(() => {
    if (user.role === "admin") load();
  }, [user.role]);

  const backupDatabase = async () => {
    try {
      const response = await api.get("/admin/backup", { responseType: "blob" });
      downloadBlob(response.data, `casefile-database-${new Date().toISOString().slice(0, 10)}.casefile-backup`);
      toast.success("Consistent database backup downloaded");
    } catch (error) {
      toast.error(errorMessage(error, "Database backup failed."));
    }
  };

  const backupComplete = async () => {
    try {
      const response = await api.get("/admin/complete-backup", { responseType: "blob" });
      downloadBlob(response.data, `casefile-complete-${new Date().toISOString().slice(0, 10)}.casefile-complete-backup`);
      const count = response.headers["x-casefile-document-count"];
      toast.success(`Complete encrypted backup downloaded${count ? ` (${count} document files)` : ""}`);
    } catch (error) {
      toast.error(errorMessage(error, "Complete backup failed."));
    }
  };

  const create = async event => {
    event.preventDefault();
    try {
      await api.post("/admin/users", form);
      setForm(blank);
      setShow(false);
      load();
      toast.success("User created");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  const toggleAssignment = (caseId, userId) => setAssignments(list => list.map(item => (
    item.id !== caseId ? item : {
      ...item,
      user_ids: item.user_ids.includes(userId)
        ? item.user_ids.filter(id => id !== userId)
        : [...item.user_ids, userId],
    }
  )));

  const saveAssignments = async item => {
    try {
      const { data } = await api.put(`/admin/cases/${item.id}/assignments`, { user_ids: item.user_ids });
      setAssignments(data);
      toast.success(`Access saved for ${item.name}`);
    } catch (error) {
      toast.error(errorMessage(error, "Could not save case access."));
    }
  };

  const restoreDatabase = async event => {
    event.preventDefault();
    if (!databaseRestoreFile || !window.confirm("Restore this database-only backup? Current data will first be preserved in an automatic safety backup, then replaced.")) return;
    const body = new FormData();
    body.append("file", databaseRestoreFile);
    try {
      await api.post("/admin/restore", body);
      toast.success("Database backup restored. Reloading the workspace…");
      window.location.reload();
    } catch (error) {
      toast.error(errorMessage(error, "Restore failed; current data was not changed."));
    }
  };

  const restoreComplete = async event => {
    event.preventDefault();
    if (!completeRestoreFile || !window.confirm("Restore this complete backup? It will replace the database and every uploaded case document. A complete encrypted safety backup will be created first.")) return;
    const body = new FormData();
    body.append("file", completeRestoreFile);
    try {
      const { data } = await api.post("/admin/complete-restore", body);
      toast.success(`Complete backup restored (${data.documents_restored} document files). Reloading the workspace…`);
      window.location.reload();
    } catch (error) {
      toast.error(errorMessage(error, "Complete restore failed; current data was not changed."));
    }
  };

  if (user.role !== "admin") {
    return <section className="workspace"><div className="casefile-panel panel-empty">Administrator access is required for security settings.</div></section>;
  }

  return <section className="workspace space-y-6">
    <div className="workspace-intro">
      <div>
        <p className="kicker">LOCAL SECURITY & RELIABILITY</p>
        <h2>Settings</h2>
        <p className="intro-copy">Users, roles, case access, session security, file controls, and recoverable backups.</p>
      </div>
      <button className="primary-button" onClick={backupComplete}><Download size={16} />Back up database + documents</button>
    </div>

    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">SECURITY STATUS</p><h3>Protection controls</h3></div><ShieldCheck size={21} /></div>
      <dl className="settings-list">{Object.entries(status).map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{String(value)}</dd></div>)}</dl>
    </section>

    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">COMPLETE BACKUP RECOVERY</p><h3>Restore database + uploaded documents</h3></div></div>
      <form className="upload-form" onSubmit={restoreComplete}>
        <input type="file" accept=".casefile-complete-backup" required onChange={event => setCompleteRestoreFile(event.target.files?.[0] || null)} />
        <small><b>Complete backup:</b> restores the database and every uploaded case document as one verified, encrypted bundle. The current database and documents are preserved first in an automatic complete safety backup.</small>
        <button className="primary-button" disabled={!completeRestoreFile}>Validate and restore complete backup</button>
      </form>
    </section>

    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">DATABASE-ONLY RECOVERY</p><h3>Restore encrypted database backup</h3><p className="module-description">Use only for older database-only backups. Uploaded document files are not included or changed.</p></div><button className="outline-button small" onClick={backupDatabase}><Download size={15} />Database-only backup</button></div>
      <form className="upload-form" onSubmit={restoreDatabase}>
        <input type="file" accept=".casefile-backup" required onChange={event => setDatabaseRestoreFile(event.target.files?.[0] || null)} />
        <button className="outline-button" disabled={!databaseRestoreFile}>Validate and restore database</button>
      </form>
    </section>

    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">ROLE-BASED ACCESS</p><h3>Office users</h3></div><button className="outline-button small" onClick={() => setShow(!show)}><Plus size={15} />Add user</button></div>
      {show && <form className="module-create-form" onSubmit={create}><div className="form-grid"><label>Name<input required value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} /></label><label>Email<input required type="email" value={form.email} onChange={event => setForm({ ...form, email: event.target.value })} /></label><label>Role<select value={form.role} onChange={event => setForm({ ...form, role: event.target.value })}><option value="professional">Insolvency Professional</option><option value="manager">Manager</option><option value="associate">Associate</option><option value="viewer">Viewer</option></select></label><label>Temporary password<input required minLength="12" type="password" value={form.password} onChange={event => setForm({ ...form, password: event.target.value })} /></label></div><div className="modal-actions"><button className="primary-button">Create user</button></div></form>}
      <div className="master-list">{users.map(item => <div key={item.id}><b>{item.name}</b><small>{item.email} · {item.role} · {item.active ? "active" : "disabled"}</small></div>)}</div>
    </section>

    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">CASE ISOLATION</p><h3>Case assignments</h3><p className="module-description">Managers, associates, and viewers can retrieve only assigned cases. Administrators and the Insolvency Professional retain firm-wide access.</p></div></div>
      <div className="assignment-list">{assignments.map(item => <div className="assignment-row" key={item.id}><div><b>{item.name}</b><small>{item.petition_number || "Case number not set"}</small></div><div className="assignment-users">{users.filter(entry => !["admin", "professional"].includes(entry.role) && entry.active).map(entry => <label key={entry.id}><input type="checkbox" checked={item.user_ids.includes(entry.id)} onChange={() => toggleAssignment(item.id, entry.id)} />{entry.name} <small>({entry.role})</small></label>)}</div><button className="outline-button small" onClick={() => saveAssignments(item)}>Save access</button></div>)}</div>
    </section>
  </section>;
}
