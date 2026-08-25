import React, { useCallback, useEffect, useState } from "react";
import { CheckSquare2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";

export default function FirmTasksView() {
  const [tasks, setTasks] = useState([]);
  const load = useCallback(async () => { try { setTasks((await api.get("/firm/tasks")).data); } catch (error) { toast.error(errorMessage(error, "Could not load firm tasks.")); } }, []);
  useEffect(() => { load(); }, [load]);
  const change = async (task, status) => { try { await api.put(`/cases/${task.case_id}/tasks/${task.id}`, { status, ...(status === "completed" ? { completed_at: new Date().toISOString() } : {}) }); load(); } catch (error) { toast.error(errorMessage(error)); } };
  return <section className="workspace"><div className="workspace-intro"><div><p className="kicker">FIRM-WIDE WORK QUEUE</p><h2>All Tasks</h2><p className="intro-copy">Assignments remain linked to their company while being prioritized across the practice.</p></div><CheckSquare2 size={28} /></div><div className="casefile-panel module-table-wrap"><table className="module-table"><thead><tr><th>Task</th><th>Company</th><th>Category</th><th>Priority</th><th>Due</th><th>Status</th></tr></thead><tbody>{tasks.map(task => <tr key={task.id}><td>{task.title}</td><td>{task.case_name || "Firm-wide"}</td><td>{task.category || "—"}</td><td>{task.priority}</td><td>{task.due_date || "—"}</td><td><select className="inline-status" value={task.status} onChange={e => change(task, e.target.value)}><option>open</option><option>in-progress</option><option>blocked</option><option>completed</option><option>cancelled</option></select></td></tr>)}{!tasks.length && <tr><td colSpan="6" className="panel-empty">No tasks recorded.</td></tr>}</tbody></table></div></section>;
}

