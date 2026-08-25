import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Download, FileCheck2, FileText, Plus, Save, Users } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "../lib/api";

const blankAgenda = { position: 1, section: "discussion", title: "", notes: "", proposed_resolution: "", voting_required: false, status: "draft" };
const blankAttendance = { member_id: "", participant_name: "", organization: "", capacity: "CoC member", email: "", present: true, attendance_mode: "Physical", voting_share_snapshot: 0, notes: "" };

export default function CocWorkspace({ caseRecord }) {
  const caseId = caseRecord.id;
  const [meetings, setMeetings] = useState([]);
  const [meetingId, setMeetingId] = useState("");
  const [workflow, setWorkflow] = useState(null);
  const [agendaForm, setAgendaForm] = useState(blankAgenda);
  const [attendanceForm, setAttendanceForm] = useState(blankAttendance);
  const [busy, setBusy] = useState("");

  const loadMeetings = useCallback(async () => {
    try {
      const { data } = await api.get(`/cases/${caseId}/coc-meetings`);
      const ordered = [...data].sort((a, b) => Number(a.meeting_number) - Number(b.meeting_number));
      setMeetings(ordered);
      setMeetingId(current => current || ordered[0]?.id || "");
    } catch (error) { toast.error(errorMessage(error, "Could not load CoC meetings.")); }
  }, [caseId]);

  const loadWorkflow = useCallback(async () => {
    if (!meetingId) { setWorkflow(null); return; }
    try {
      const { data } = await api.get(`/cases/${caseId}/coc-meetings/${meetingId}/workflow`);
      setWorkflow(data);
      setAgendaForm(form => ({ ...form, position: Math.max(1, ...data.agenda.map(item => Number(item.position) + 1)) }));
    } catch (error) { toast.error(errorMessage(error, "Could not load this meeting workflow.")); }
  }, [caseId, meetingId]);

  useEffect(() => { loadMeetings(); }, [loadMeetings]);
  useEffect(() => { loadWorkflow(); }, [loadWorkflow]);

  const meeting = workflow?.meeting;
  const snapshotIssued = Boolean(meeting?.notice_snapshot?.issued_at);
  const discussionComplete = Boolean(workflow?.agenda?.length) && workflow.agenda.every(item => String(item.discussion || "").trim());

  const addAgenda = async event => {
    event.preventDefault(); setBusy("agenda");
    try {
      await api.post(`/cases/${caseId}/coc-agenda-items`, { ...agendaForm, meeting_id: meetingId });
      setAgendaForm({ ...blankAgenda, position: Number(agendaForm.position) + 1 });
      await loadWorkflow(); toast.success("Agenda item added in meeting order");
    } catch (error) { toast.error(errorMessage(error, "Could not add the agenda item.")); }
    finally { setBusy(""); }
  };

  const updateAgenda = async (item, change) => {
    try {
      await api.put(`/cases/${caseId}/coc-agenda-items/${item.id}`, change);
      setWorkflow(value => ({ ...value, agenda: value.agenda.map(row => row.id === item.id ? { ...row, ...change } : row) }));
      toast.success("Agenda record saved");
    } catch (error) { toast.error(errorMessage(error, "Could not save the agenda record.")); }
  };

  const selectMember = id => {
    const member = workflow?.members.find(item => item.id === id);
    setAttendanceForm({
      ...attendanceForm, member_id: id,
      participant_name: member?.authorized_representative || member?.contact_name || "",
      organization: member?.organization || member?.contact_name || "",
      email: member?.email || "", voting_share_snapshot: member?.voting_share || 0,
    });
  };

  const addAttendance = async event => {
    event.preventDefault(); setBusy("attendance");
    try {
      await api.post(`/cases/${caseId}/coc-attendance`, { ...attendanceForm, meeting_id: meetingId });
      setAttendanceForm(blankAttendance); await loadWorkflow(); toast.success("Attendance recorded with voting-share snapshot");
    } catch (error) { toast.error(errorMessage(error, "Could not record attendance.")); }
    finally { setBusy(""); }
  };

  const issueNotice = async () => {
    if (!window.confirm("Issue this notice and freeze the current agenda/member snapshot? Later edits will not rewrite the historical notice chain.")) return;
    setBusy("issue");
    try {
      await api.post(`/cases/${caseId}/coc-meetings/${meetingId}/issue-notice`);
      await loadWorkflow(); toast.success("Notice issued and agenda snapshot frozen");
    } catch (error) { toast.error(errorMessage(error, "Could not issue the notice.")); }
    finally { setBusy(""); }
  };

  const generate = async (documentType, status) => {
    setBusy(`${documentType}-${status}`);
    try {
      const { data } = await api.post(`/cases/${caseId}/coc-meetings/${meetingId}/documents`, { document_type: documentType, status });
      if (documentType === "notice" && status === "final") await loadWorkflow();
      toast.success(`${documentType === "notice" ? "Notice" : "Minutes"} version ${data.version} stored in Documents`);
    } catch (error) { toast.error(errorMessage(error, "Could not generate the retained-template document.")); }
    finally { setBusy(""); }
  };

  const quorum = workflow?.quorum || { present_voting_share: 0, threshold: 33, met: false };
  const sortedAgenda = useMemo(() => [...(workflow?.agenda || [])].sort((a, b) => Number(a.position) - Number(b.position)), [workflow]);

  if (!meetings.length) return <section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">COC WORKFLOW</p><h3>Create a meeting first</h3></div><FileText size={20} /></div><p className="panel-empty">Use “CoC meetings” above to add the meeting date, time, mode and venue. The agenda, notice, attendance and minutes workflow will then open here.</p></section>;

  return <div className="space-y-6" data-testid="coc-workflow">
    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">MEETING CONTROL</p><h3>Agenda → notice → attendance → minutes</h3></div><FileCheck2 size={20} /></div>
      <label className="module-label">Selected meeting<select value={meetingId} onChange={event => setMeetingId(event.target.value)}>{meetings.map(item => <option key={item.id} value={item.id}>Meeting {item.meeting_number} · {String(item.meeting_at).replace("T", " ")}</option>)}</select></label>
      {meeting && <div className="coc-status-grid"><span><b>Notice</b><small>{snapshotIssued ? `Issued ${meeting.notice_snapshot.issued_at.slice(0, 10)}` : "Not issued"}</small></span><span><b>Agenda</b><small>{sortedAgenda.length} ordered item(s)</small></span><span className={quorum.met ? "coc-good" : "coc-warn"}><b>Quorum calculation</b><small>{Number(quorum.present_voting_share).toFixed(4)}% / {Number(quorum.threshold).toFixed(4)}% · {quorum.met ? "threshold met" : "not met"}</small></span><span><b>Minutes readiness</b><small>{discussionComplete ? "Manual discussions complete" : "Discussion entry pending"}</small></span></div>}
      <div className="coc-action-row"><button className="outline-button" onClick={() => generate("notice", "review")} disabled={busy || !sortedAgenda.length}><FileText size={16} />Generate notice for review</button><button className="primary-button" onClick={issueNotice} disabled={busy || snapshotIssued || !sortedAgenda.length}><Check size={16} />Issue and freeze notice</button><button className="outline-button" onClick={() => generate("notice", "final")} disabled={busy || !sortedAgenda.length}><Download size={16} />Store final notice</button><button className="primary-button" onClick={() => generate("minutes", "review")} disabled={busy || !snapshotIssued || !discussionComplete}><FileCheck2 size={16} />Generate minutes for review</button><button className="outline-button" onClick={() => generate("minutes", "final")} disabled={busy || !snapshotIssued || !discussionComplete}><Save size={16} />Store final minutes</button></div>
      <small>Generated DOCX files use the retained first/subsequent notice or minutes precedent and are saved as new versions in this company’s Documents tab.</small>
    </section>

    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">ORDERED AGENDA</p><h3>Notice-to-minutes agenda records</h3></div><Plus size={20} /></div>
      <form className="module-create-form" onSubmit={addAgenda}><div className="form-grid"><label>Position<input type="number" min="1" required value={agendaForm.position} onChange={e => setAgendaForm({ ...agendaForm, position: e.target.value })} /></label><label>Section<select value={agendaForm.section} onChange={e => setAgendaForm({ ...agendaForm, section: e.target.value, voting_required: e.target.value === "voting" })}><option value="discussion">Discussion</option><option value="voting">Voting</option></select></label><label className="full">Agenda title<input required value={agendaForm.title} onChange={e => setAgendaForm({ ...agendaForm, title: e.target.value })} /></label><label className="full">Proposed resolution, if any<textarea value={agendaForm.proposed_resolution} onChange={e => setAgendaForm({ ...agendaForm, proposed_resolution: e.target.value })} /></label></div><button className="outline-button" disabled={busy === "agenda"}><Plus size={15} />Add agenda item</button></form>
      <div className="coc-agenda-list">{sortedAgenda.map((item, index) => <article key={item.id}><header><span className="status-chip">Agenda {index + 1}</span><b>{item.title}</b>{item.voting_required && <small>Voting item</small>}</header><label>Manual discussion — required for minutes<textarea value={item.discussion || ""} onChange={e => setWorkflow(value => ({ ...value, agenda: value.agenda.map(row => row.id === item.id ? { ...row, discussion: e.target.value } : row) }))} onBlur={e => updateAgenda(item, { discussion: e.target.value })} /></label><label>Decision<textarea value={item.decision || ""} onChange={e => setWorkflow(value => ({ ...value, agenda: value.agenda.map(row => row.id === item.id ? { ...row, decision: e.target.value } : row) }))} onBlur={e => updateAgenda(item, { decision: e.target.value })} /></label><label>Final resolution text<textarea value={item.resolution_text || ""} placeholder={item.proposed_resolution || "No proposed resolution"} onChange={e => setWorkflow(value => ({ ...value, agenda: value.agenda.map(row => row.id === item.id ? { ...row, resolution_text: e.target.value } : row) }))} onBlur={e => updateAgenda(item, { resolution_text: e.target.value })} /></label></article>)}</div>
    </section>

    <section className="casefile-panel">
      <div className="panel-heading"><div><p className="kicker">ATTENDANCE & QUORUM</p><h3>Actual participants</h3></div><Users size={20} /></div>
      <form className="module-create-form" onSubmit={addAttendance}><div className="form-grid"><label>CoC member<select value={attendanceForm.member_id} onChange={e => selectMember(e.target.value)}><option value="">Manual participant</option>{workflow?.members.map(item => <option key={item.id} value={item.id}>{item.organization || item.contact_name || item.authorized_representative || "CoC member"} · {item.voting_share}%</option>)}</select></label><label>Participant name<input required value={attendanceForm.participant_name} onChange={e => setAttendanceForm({ ...attendanceForm, participant_name: e.target.value })} /></label><label>Organization<input value={attendanceForm.organization} onChange={e => setAttendanceForm({ ...attendanceForm, organization: e.target.value })} /></label><label>Capacity<input value={attendanceForm.capacity} onChange={e => setAttendanceForm({ ...attendanceForm, capacity: e.target.value })} /></label><label>Mode<select value={attendanceForm.attendance_mode} onChange={e => setAttendanceForm({ ...attendanceForm, attendance_mode: e.target.value })}><option>Physical</option><option>Video conference</option><option>Audio conference</option></select></label><label>Voting share snapshot (%)<input type="number" min="0" max="100" step="0.0001" value={attendanceForm.voting_share_snapshot} onChange={e => setAttendanceForm({ ...attendanceForm, voting_share_snapshot: e.target.value })} /></label></div><button className="outline-button" disabled={busy === "attendance"}><Plus size={15} />Record attendance</button></form>
      <div className="master-list">{workflow?.attendance.map(item => <div key={item.id}><b>{item.participant_name}</b><small>{item.organization || item.capacity} · {item.attendance_mode} · {item.voting_share_snapshot}% voting share</small></div>)}{!workflow?.attendance.length && <p className="panel-empty">No attendance recorded.</p>}</div>
    </section>
  </div>;
}
