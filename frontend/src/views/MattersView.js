import React, { useState } from "react";
import { Plus, Trash2, ArrowRight, Save, CheckCircle } from "lucide-react";
import { toast } from "sonner";
import { api } from "../lib/api";

const TIMELINE_STEPS = [
  { day: 0, provision: "Section 16(1)", label: "Commencement of CIRP and appointment of IRP" },
  { day: 3, provision: "Regulation 6(1)", label: "Public announcement inviting claims" },
  { day: 14, provision: "Section 15(1)(c) / Regulations 6(2)(c), 12(1)", label: "Submission of claims" },
  { day: 21, provision: "Regulation 13(1)", label: "Verification of claims received under regulation 12(1)" },
  { day: 23, provision: "Section 21(6A)(b) / Regulation 16A", label: "Application for appointment of authorised representative, if applicable" },
  { day: 23, provision: "Regulation 17(1)", label: "Report certifying constitution of CoC" },
  { day: 30, provision: "Section 22(1) / Regulation 19(2)", label: "First meeting of the CoC" },
  { day: 30, provision: "Section 22(2)", label: "CoC resolution to appoint the RP" },
  { day: 40, provision: "Regulation 17(3)", label: "IRP continues as RP if RP has not been appointed" },
  { day: 47, provision: "Regulation 27", label: "Appointment of registered valuers" },
  { day: 60, provision: "Regulation 36A", label: "Publish brief particulars inviting expression of interest" },
  { day: 75, provision: "Regulation 35A", label: "Opinion on preferential and other transactions" },
  { day: 75, provision: "Regulation 36A", label: "Last date for submission of expressions of interest" },
  { day: 85, provision: "Regulation 36A", label: "Provisional list of resolution applicants" },
  { day: 90, provision: "Regulation 36A", label: "Last date for objections to provisional list" },
  { day: 95, provision: "Regulation 36(1)", label: "Submission of Information Memorandum to CoC" },
  { day: 100, provision: "Regulation 36A", label: "Final list of resolution applicants" },
  { day: 105, provision: "Regulation 36B", label: "Issue RFRP, evaluation matrix and Information Memorandum" },
  { day: 115, provision: "Regulation 35A", label: "Determination of preferential and other transactions" },
  { day: 130, provision: "Regulation 35A", label: "Application to AA for relief concerning avoidance transactions" },
  { day: 135, provision: "Regulation 36B", label: "Receipt of resolution plans" },
  { day: 150, provision: "Regulation 39(4)", label: "Submission of CoC-approved resolution plan to AA" },
  { day: 180, provision: "Section 31(1)", label: "Approval of resolution plan by AA" },
];

function addDays(dateStr, days) {
  if (!dateStr) return "";
  const [year, month, date] = dateStr.split("-").map(Number);
  const d = new Date(year, month - 1, date);
  if (Number.isNaN(d.getTime())) return "";
  d.setDate(d.getDate() + days);
  const pad = value => String(value).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function displayDate(dateStr) {
  if (!dateStr) return "Pending";
  const [year, month, date] = dateStr.split("-").map(Number);
  return new Date(year, month - 1, date).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export default function MattersView({ matters, setMatters }) {
  const [activeMatter, setActiveMatter] = useState(null);
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
      const { data } = await api.post("/matters", { name, values: { cd_name: name }, tables: {}, timeline: {} });
      setMatters([...matters, data]);
      toast.success("Case created");
    } catch (e) {
      toast.error("Could not create case");
    }
  };

  const del = async (id) => {
    if (!window.confirm("Delete this case forever?")) return;
    try {
      await api.delete(`/matters/${id}`);
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
      const matterName = values.cd_name?.trim() || matter.name;
      await api.put(`/matters/${matter.id}`, { name: matterName, values, tables: matter.tables || {}, timeline });
      updateMatter({ ...matter, name: matterName, values, timeline });
      toast.success("Case saved");
    } catch (e) {
      toast.error("Could not save case");
    }
    setBusy(false);
  };

  const setVal = (k, v) => setValues(prev => {
    if (k === "cirp_commencement_date") return { ...prev, cirp_commencement_date: v, cirp_order_date: v };
    if (k === "nclt_order_date") return { ...prev, nclt_order_date: v, cirp_order_date: v };
    return { ...prev, [k]: v };
  });
  
  const toggleTimeline = (stepKey) => {
    setTimeline(prev => ({ ...prev, [stepKey]: !prev[stepKey] }));
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
        {["overview", "timeline"].map(t => (
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
              <div className="form-group"><label className="label">Company Petition Number</label><input className="input" value={values.cp_ib_number || ""} onChange={e => setVal("cp_ib_number", e.target.value)} /></div>
              <div className="form-group"><label className="label">Applicant / Petitioning Creditor</label><input className="input" value={values.applicant_name || ""} onChange={e => setVal("applicant_name", e.target.value)} /></div>
              <div className="form-group"><label className="label">Creditor Type</label><select className="input" value={values.creditor_type || ""} onChange={e => setVal("creditor_type", e.target.value)}><option value="">Select type</option><option value="financial">Financial creditor</option><option value="operational">Operational creditor</option><option value="corporate-applicant">Corporate applicant</option></select></div>
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
                    const stepKey = `${step.day}-${step.provision}-${step.label}`;
                    const isDone = !!timeline[stepKey];
                    const deadline = addDays(values.cirp_commencement_date, step.day);
                    const today = new Date(); today.setHours(0, 0, 0, 0);
                    const [year, month, date] = deadline ? deadline.split("-").map(Number) : [];
                    const isOverdue = deadline && !isDone && new Date(year, month - 1, date) < today;
                    return (
                      <tr key={idx} className={`hover:bg-gray-50 transition-colors ${isDone ? 'bg-green-50/30' : ''}`}>
                        <td className="p-4">
                          <button aria-label={`${isDone ? "Mark incomplete" : "Mark complete"}: ${step.label}`} onClick={() => toggleTimeline(stepKey)} className={`w-6 h-6 flex items-center justify-center rounded-full border ${isDone ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 hover:border-blue-500 text-transparent hover:text-blue-200'}`}>
                            <CheckCircle size={16} />
                          </button>
                        </td>
                        <td className="p-4 font-medium text-gray-900">T+{step.day}</td>
                        <td className={`p-4 ${isDone ? 'text-gray-500 line-through' : 'text-gray-900'}`}><div>{step.label}</div><div className="text-xs text-gray-400 mt-1">{step.provision}</div></td>
                        <td className={`p-4 font-medium ${isOverdue ? 'text-red-600' : isDone ? 'text-gray-400' : 'text-gray-600'}`}>
                          {displayDate(deadline)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-gray-500">Model timeline based on Regulation 40A of the CIRP Regulations as amended up to 9 June 2026. Matter-specific orders, exclusions, extensions, and later amendments must be reviewed separately.</p>
          </div>
        )}
      </div>
    </div>
  );
}
