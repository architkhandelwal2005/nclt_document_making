import React, { useState, useEffect } from "react";
import axios from "axios";
import { Plus, Trash2, ArrowRight, Save, Calendar, CheckCircle, FileText } from "lucide-react";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const TIMELINE_STEPS = [
  { day: 0, label: "Date of NCLT Order" },
  { day: 3, label: "Public announcement inviting claims" },
  { day: 14, label: "Submission of claims" },
  { day: 21, label: "Verification of claims" },
  { day: 23, label: "Report certifying constitution of CoC" },
  { day: 30, label: "1st meeting of the CoC" },
  { day: 40, label: "Appointment of RP" },
  { day: 90, label: "Submission of late claims" },
  { day: 115, label: "Issue Information Memorandum" },
  { day: 180, label: "Submission of Resolution Plan to AA" },
];

function addDays(dateStr, days) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  d.setDate(d.getDate() + days);
  return d.toISOString().split('T')[0];
}

export default function MattersView({ matters, setMatters }) {
  const [activeMatter, setActiveMatter] = useState(null);
  const [tab, setTab] = useState("overview");

  if (activeMatter) {
    return <MatterDetail matter={activeMatter} onBack={() => setActiveMatter(null)} updateMatter={(m) => {
      setActiveMatter(m);
      setMatters(matters.map(x => x.id === m.id ? m : x));
    }} />;
  }

  const create = async () => {
    const name = window.prompt("Enter Corporate Debtor Name:");
    if (!name) return;
    try {
      const { data } = await axios.post(`${API}/matters`, { name, values: { cd_name: name }, tables: {}, timeline: {} });
      setMatters([...matters, data]);
      toast.success("Case created");
    } catch (e) {
      toast.error("Could not create case");
    }
  };

  const del = async (id) => {
    if (!window.confirm("Delete this case forever?")) return;
    try {
      await axios.delete(`${API}/matters/${id}`);
      setMatters(matters.filter(m => m.id !== id));
      toast.success("Case deleted");
    } catch (e) {
      toast.error("Could not delete case");
    }
  };

  return (
    <div className="fade-in max-w-5xl mx-auto space-y-6">
      <div className="flex justify-between items-end mb-8">
        <div>
          <p className="kicker">ACTIVE PORTFOLIO</p>
          <h2 className="text-3xl font-bold tracking-tight">Companies & Cases</h2>
        </div>
        <button className="primary-button" onClick={create}><Plus size={17} />New Case</button>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {matters.map(m => (
          <div key={m.id} className="card p-6 flex flex-col gap-4 hover:border-blue-500 transition-colors cursor-pointer group" onClick={() => setActiveMatter(m)}>
            <div>
              <h3 className="font-semibold text-xl mb-1 group-hover:text-blue-600">{m.name}</h3>
              <p className="text-sm text-gray-500">CIN: {m.values?.cin || "N/A"}</p>
            </div>
            <div className="flex justify-between items-center mt-4">
              <span className="text-sm font-medium bg-blue-50 text-blue-700 px-3 py-1 rounded-full">
                CIRP: {m.values?.cirp_commencement_date || "Pending"}
              </span>
              <button className="icon-btn text-gray-400 hover:text-red-500 hover:bg-red-50" onClick={(e) => { e.stopPropagation(); del(m.id); }}>
                <Trash2 size={18} />
              </button>
            </div>
          </div>
        ))}
        {matters.length === 0 && <div className="col-span-full text-center py-12 text-gray-400 border-2 border-dashed rounded-lg">No companies added yet. Click New Case to start.</div>}
      </div>
    </div>
  );
}

function MatterDetail({ matter, onBack, updateMatter }) {
  const [tab, setTab] = useState("overview");
  const [busy, setBusy] = useState(false);
  const [values, setValues] = useState(matter.values || {});
  const [timeline, setTimeline] = useState(matter.timeline || {});

  const save = async () => {
    setBusy(true);
    try {
      await axios.put(`${API}/matters/${matter.id}`, { name: matter.name, values, tables: matter.tables, timeline });
      updateMatter({ ...matter, values, timeline });
      toast.success("Case saved");
    } catch (e) {
      toast.error("Could not save case");
    }
    setBusy(false);
  };

  const setVal = (k, v) => setValues(prev => ({ ...prev, [k]: v }));
  
  const toggleTimeline = (stepIdx) => {
    setTimeline(prev => ({ ...prev, [stepIdx]: !prev[stepIdx] }));
  };

  return (
    <div className="fade-in max-w-5xl mx-auto flex flex-col h-[calc(100vh-80px)]">
      <div className="flex items-center gap-4 mb-6">
        <button className="icon-btn" onClick={onBack}><ArrowRight size={20} className="rotate-180" /></button>
        <div>
          <h2 className="text-2xl font-bold">{matter.name}</h2>
          <p className="text-sm text-gray-500">Master Case File</p>
        </div>
        <button className="primary-button ml-auto" onClick={save} disabled={busy}><Save size={16} /> Save Changes</button>
      </div>

      <div className="flex border-b border-gray-200 mb-6">
        {["overview", "timeline", "compliance"].map(t => (
          <button key={t} className={`px-6 py-3 font-medium text-sm border-b-2 transition-colors ${tab === t ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-900"}`} onClick={() => setTab(t)}>
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto pb-12">
        {tab === "overview" && (
          <div className="card p-6 space-y-6">
            <h3 className="text-lg font-semibold mb-4">Corporate Debtor Details</h3>
            <div className="grid grid-cols-2 gap-6">
              <div className="form-group"><label className="label">Corporate Debtor Name</label><input className="input" value={values.cd_name || ""} onChange={e => setVal("cd_name", e.target.value)} /></div>
              <div className="form-group"><label className="label">CIN</label><input className="input" value={values.cin || ""} onChange={e => setVal("cin", e.target.value)} /></div>
              <div className="form-group col-span-2"><label className="label">Registered Office Address</label><textarea className="input" value={values.cd_address || ""} onChange={e => setVal("cd_address", e.target.value)} /></div>
              <div className="form-group"><label className="label">Registered Email</label><input className="input" value={values.cd_email || ""} onChange={e => setVal("cd_email", e.target.value)} /></div>
              <div className="form-group"><label className="label">NCLT Bench</label><input className="input" value={values.nclt_bench || ""} onChange={e => setVal("nclt_bench", e.target.value)} /></div>
              <div className="form-group"><label className="label">Date of NCLT Order</label><input type="date" className="input" value={values.nclt_order_date || ""} onChange={e => setVal("nclt_order_date", e.target.value)} /></div>
              <div className="form-group"><label className="label">CIRP Commencement Date</label><input type="date" className="input" value={values.cirp_commencement_date || ""} onChange={e => setVal("cirp_commencement_date", e.target.value)} /></div>
            </div>
          </div>
        )}

        {tab === "timeline" && (
          <div className="space-y-4">
            {!values.cirp_commencement_date && (
              <div className="p-4 bg-yellow-50 text-yellow-800 rounded-md mb-4 border border-yellow-200">
                Please set the <strong>CIRP Commencement Date</strong> in the Overview tab to calculate the timeline automatically.
              </div>
            )}
            
            <div className="card p-0 overflow-hidden">
              <table className="w-full text-left text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="p-4 font-semibold text-gray-600">Status</th>
                    <th className="p-4 font-semibold text-gray-600">T+ Day</th>
                    <th className="p-4 font-semibold text-gray-600">Activity</th>
                    <th className="p-4 font-semibold text-gray-600">Calculated Deadline</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {TIMELINE_STEPS.map((step, idx) => {
                    const isDone = !!timeline[idx];
                    const deadline = addDays(values.cirp_commencement_date, step.day);
                    const isOverdue = deadline && !isDone && new Date(deadline) < new Date();
                    return (
                      <tr key={idx} className={`hover:bg-gray-50 transition-colors ${isDone ? 'bg-green-50/30' : ''}`}>
                        <td className="p-4">
                          <button onClick={() => toggleTimeline(idx)} className={`w-6 h-6 flex items-center justify-center rounded-full border ${isDone ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 hover:border-blue-500 text-transparent hover:text-blue-200'}`}>
                            <CheckCircle size={16} />
                          </button>
                        </td>
                        <td className="p-4 font-medium text-gray-900">T+{step.day}</td>
                        <td className={`p-4 ${isDone ? 'text-gray-500 line-through' : 'text-gray-900'}`}>{step.label}</td>
                        <td className={`p-4 font-medium ${isOverdue ? 'text-red-600' : isDone ? 'text-gray-400' : 'text-gray-600'}`}>
                          {deadline ? new Date(deadline).toLocaleDateString() : "Pending"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
        
        {tab === "compliance" && (
          <div className="card p-12 text-center text-gray-500 border-2 border-dashed">
            <FileText size={48} className="mx-auto mb-4 text-gray-300" />
            <h3 className="text-lg font-medium text-gray-900">Compliance Checklist</h3>
            <p className="mt-1">Track CIRP 1, CIRP 2, and Form A filings here. (Coming soon)</p>
          </div>
        )}
      </div>
    </div>
  );
}
