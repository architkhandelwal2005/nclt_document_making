import React, { useEffect, useState } from "react";
import { toast } from "sonner";
import { Save } from "lucide-react";
import { api } from "../lib/api";

export default function ProfileView({ profile, setProfile }) {
  const [busy, setBusy] = useState(false);
  const [data, setData] = useState(profile || {});

  useEffect(() => { setData(profile || {}); }, [profile]);

  const save = async () => {
    setBusy(true);
    try {
      await api.put("/profile", data);
      setProfile(data);
      toast.success("Profile saved successfully");
    } catch (e) {
      toast.error("Could not save profile");
    }
    setBusy(false);
  };

  const setVal = (k, v) => setData(prev => ({ ...prev, [k]: v }));

  return (
    <div className="fade-in max-w-2xl mx-auto space-y-6">
      <div className="mb-8">
        <p className="kicker">ACCOUNT & SETTINGS</p>
        <h2 className="text-3xl font-bold tracking-tight">My Profile</h2>
        <p className="text-gray-500 mt-2">Information saved here will automatically be injected into all generated documents and filings.</p>
      </div>

      <div className="card p-6 space-y-6">
        <div className="grid grid-cols-2 gap-6">
          <div className="form-group">
            <label className="label">Salutation</label>
            <input className="input" value={data.ip_salutation || ""} onChange={e => setVal("ip_salutation", e.target.value)} placeholder="e.g. Mr., Ms., Dr." />
          </div>
          <div className="form-group">
            <label className="label">IP Name</label>
            <input className="input" value={data.ip_name || ""} onChange={e => setVal("ip_name", e.target.value)} placeholder="e.g. Navin Khandelwal" />
          </div>
          <div className="form-group">
            <label className="label">IBBI Registration Number</label>
            <input className="input" value={data.ibbi_reg_no || ""} onChange={e => setVal("ibbi_reg_no", e.target.value)} placeholder="IBBI/IPA-..." />
          </div>
          <div className="form-group col-span-2">
            <label className="label">Registered Address</label>
            <textarea className="input min-h-[80px]" value={data.ip_reg_address || ""} onChange={e => setVal("ip_reg_address", e.target.value)} />
          </div>
          <div className="form-group">
            <label className="label">Registered Email</label>
            <input type="email" className="input" value={data.ip_email || ""} onChange={e => setVal("ip_email", e.target.value)} />
          </div>
          <div className="form-group">
            <label className="label">Phone Number</label>
            <input type="tel" className="input" value={data.ip_phone || ""} onChange={e => setVal("ip_phone", e.target.value)} />
          </div>
          <div className="form-group">
            <label className="label">Process Specific Email</label>
            <input className="input" value={data.process_email || ""} onChange={e => setVal("process_email", e.target.value)} />
          </div>
          <div className="form-group">
            <label className="label">AFA Validity Date</label>
            <input type="date" className="input" value={data.afa_validity || ""} onChange={e => setVal("afa_validity", e.target.value)} />
          </div>
        </div>
        
        <div className="pt-4 border-t border-gray-100 flex justify-end">
          <button className="primary-button" onClick={save} disabled={busy}>
            <Save size={16} /> Save Profile
          </button>
        </div>
      </div>
    </div>
  );
}
