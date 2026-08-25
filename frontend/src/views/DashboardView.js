import React from "react";
import { AlertTriangle, ArrowRight, BriefcaseBusiness, CalendarDays, CheckSquare2, Scale } from "lucide-react";

const niceDate = value => value ? new Date(value).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "Not set";

export default function DashboardView({ dashboard, cases, onOpenCase, onNewCase }) {
  const counts = dashboard?.counts || {};
  const metrics = [
    ["Active cases", counts.active_cases || 0, BriefcaseBusiness, "Portfolio"],
    ["Overdue tasks", counts.overdue_tasks || 0, CheckSquare2, "Action required"],
    ["Overdue deadlines", counts.overdue_deadlines || 0, AlertTriangle, "Compliance risk"],
    ["Pending claims", counts.pending_claims || 0, Scale, "Under review"],
  ];
  return (
    <section className="workspace space-y-8" data-testid="firm-dashboard">
      <div className="workspace-intro">
        <div><p className="kicker">FIRM CONTROL CENTRE</p><h2>Work requiring attention.</h2><p className="intro-copy">Select a company to enter its case workspace. Firm-wide tasks, hearings, and compliance risks remain visible here.</p></div>
        <button className="primary-button" onClick={onNewCase} data-testid="dashboard-new-case-button">New case <ArrowRight size={16} /></button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {metrics.map(([label, value, Icon, hint]) => <article className="casefile-stat" key={label}><div className="flex justify-between"><span>{label}</span><Icon size={18} /></div><strong>{value}</strong><small>{hint}</small></article>)}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="casefile-panel">
          <div className="panel-heading"><div><p className="kicker">ACTIVE PORTFOLIO</p><h3>Companies and cases</h3></div><BriefcaseBusiness size={20} /></div>
          <div className="divide-y divide-stone-200">
            {cases.slice(0, 8).map(item => <button className="dashboard-row" key={item.id} onClick={() => onOpenCase(item)} data-testid={`dashboard-case-${item.id}`}><span><b>{item.name}</b><small>{item.petition_number || item.internal_reference || "Reference not set"}</small></span><span className="status-chip">{item.current_stage || item.status}</span><ArrowRight size={16} /></button>)}
            {!cases.length && <p className="panel-empty">No cases yet. Create the first company workspace.</p>}
          </div>
        </section>
        <section className="casefile-panel">
          <div className="panel-heading"><div><p className="kicker">NEXT ACTIONS</p><h3>Tasks and hearings</h3></div><CalendarDays size={20} /></div>
          <div className="divide-y divide-stone-200">
            {(dashboard?.tasks || []).slice(0, 5).map(task => <div className="dashboard-info-row" key={task.id}><span><b>{task.title}</b><small>{task.case_name || "Firm-wide"}</small></span><time>{niceDate(task.due_date)}</time></div>)}
            {(dashboard?.hearings || []).slice(0, 3).map(hearing => <div className="dashboard-info-row" key={hearing.id}><span><b>{hearing.purpose || "Hearing"}</b><small>{hearing.case_name}</small></span><time>{niceDate(hearing.hearing_at)}</time></div>)}
            {!(dashboard?.tasks?.length || dashboard?.hearings?.length) && <p className="panel-empty">No upcoming work has been recorded.</p>}
          </div>
        </section>
      </div>
    </section>
  );
}

