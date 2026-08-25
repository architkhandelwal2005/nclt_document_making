import React, { useCallback, useEffect, useState } from "react";
import "@/App.css";
import { BriefcaseBusiness, CalendarDays, Database, LayoutDashboard, ListTodo, Lock, LogOut, Settings, UserCircle } from "lucide-react";
import { toast, Toaster } from "sonner";
import { api, errorMessage, TOKEN_KEY } from "./lib/api";
import DashboardView from "./views/DashboardView";
import CasesView from "./views/CasesView";
import CaseWorkspace from "./views/CaseWorkspace";
import ProfileView from "./views/ProfileView";
import FirmTasksView from "./views/FirmTasksView";
import CalendarView from "./views/CalendarView";
import MastersView from "./views/MastersView";
import SettingsView from "./views/SettingsView";

function Login({ onSuccess }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async event => {
    event.preventDefault(); setBusy(true);
    try {
      const { data } = await api.post("/auth/login", { email, password });
      localStorage.setItem(TOKEN_KEY, data.access_token); onSuccess(data.user);
      toast.success("Signed in securely");
    } catch (error) { toast.error(errorMessage(error, "Login failed.")); }
    finally { setBusy(false); }
  };
  return <div className="login-shell"><div className="login-brand"><span className="brand-mark login-mark">N</span><p className="eyebrow">CASEFILE / PRACTICE MANAGEMENT</p><h1>One operating system<br /><em>for every assignment.</em></h1><p className="login-copy">Manage companies, compliance, tasks, claims, hearings, documents, communications, and costs in isolated case workspaces.</p></div><form className="login-card" onSubmit={submit} data-testid="login-form"><p className="kicker">SECURE LOCAL ACCESS</p><h2>Casefile</h2><label>Email<input data-testid="login-email-input" type="email" autoComplete="username" value={email} onChange={e => setEmail(e.target.value)} required /></label><label>Password<input data-testid="login-password-input" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required /></label><button className="primary-button login-submit" data-testid="login-submit-button" disabled={busy}><Lock size={16} />{busy ? "Signing in…" : "Sign in"}</button><small className="login-hint">All operational data remains on this computer.</small></form></div>;
}

const nav = [
  ["dashboard", "Dashboard", LayoutDashboard], ["cases", "Cases", BriefcaseBusiness],
  ["tasks", "All Tasks", ListTodo], ["calendar", "Calendar", CalendarDays],
  ["masters", "Firm Masters", Database], ["profile", "My Profile", UserCircle],
  ["settings", "Settings", Settings],
];

export default function App() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  const [view, setView] = useState("dashboard");
  const [cases, setCases] = useState([]);
  const [dashboard, setDashboard] = useState({});
  const [profile, setProfile] = useState({});
  const [activeCase, setActiveCase] = useState(null);
  const [startCreating, setStartCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const [caseResponse, dashboardResponse, profileResponse] = await Promise.all([api.get("/cases"), api.get("/dashboard"), api.get("/profile")]);
      setCases(caseResponse.data); setDashboard(dashboardResponse.data); setProfile(profileResponse.data);
    } catch (error) { toast.error(errorMessage(error, "Could not load the local workspace.")); }
  }, []);

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) { setChecking(false); return; }
    api.get("/auth/me").then(({ data }) => setUser(data)).finally(() => setChecking(false));
  }, []);
  useEffect(() => { if (user) load(); }, [user, load]);
  useEffect(() => {
    const unauthorized = () => { setUser(null); setActiveCase(null); };
    window.addEventListener("casefile:unauthorized", unauthorized);
    return () => window.removeEventListener("casefile:unauthorized", unauthorized);
  }, []);

  const openCase = record => { setActiveCase(record); setStartCreating(false); };
  const updateCase = record => { setActiveCase(record); setCases(list => list.map(item => item.id === record.id ? record : item)); load(); };
  const logout = () => { localStorage.removeItem(TOKEN_KEY); setUser(null); setActiveCase(null); toast.success("Signed out"); };
  const chooseView = id => { setView(id); setActiveCase(null); setStartCreating(false); };

  if (checking) return <div className="app-loading">Loading secure workspace…</div>;
  if (!user) return <><Login onSuccess={setUser} /><Toaster position="bottom-right" /></>;
  const initials = user.name.split(" ").map(part => part[0]).slice(0, 2).join("").toUpperCase();
  const titles = { dashboard: "Firm Dashboard", cases: "Companies & Cases", tasks: "All Tasks", calendar: "Firm Calendar", masters: "Firm Masters", profile: "Professional Profile", settings: "Settings" };

  return <div className="app-shell"><aside className="rail"><div className="brand"><span className="brand-mark">N</span><div><strong>Casefile</strong><small>PRACTICE OS</small></div></div><div className="rail-rule" /><nav>{nav.map(([id, label, Icon]) => <button key={id} className={!activeCase && view === id ? "nav-item active" : "nav-item"} onClick={() => chooseView(id)} data-testid={`nav-${id}-button`}><Icon size={17} />{label}</button>)}</nav><div className="rail-footer"><span className="avatar">{initials}</span><div><b>{user.name}</b><small>{user.role.toUpperCase()}</small></div><button className="logout-btn" onClick={logout} title="Sign out" data-testid="logout-button"><LogOut size={15} /></button></div></aside><main className="main-content"><header className="topbar"><div><p className="eyebrow">{activeCase ? "COMPANY WORKSPACE" : "FIRM OPERATIONS"}</p><h1 data-testid="page-title">{activeCase ? activeCase.name : titles[view]}</h1></div><div className="top-status"><span className="status-dot" />Local database</div></header>{activeCase ? <CaseWorkspace caseRecord={activeCase} onBack={() => { setActiveCase(null); setView("cases"); load(); }} onUpdated={updateCase} /> : view === "dashboard" ? <DashboardView dashboard={dashboard} cases={cases} onOpenCase={openCase} onNewCase={() => { setView("cases"); setStartCreating(true); }} /> : view === "cases" ? <CasesView cases={cases} setCases={setCases} onOpenCase={openCase} startCreating={startCreating} onCreatingChange={setStartCreating} /> : view === "profile" ? <ProfileView profile={profile} setProfile={setProfile} /> : view === "tasks" ? <FirmTasksView /> : view === "calendar" ? <CalendarView /> : view === "masters" ? <MastersView /> : <SettingsView user={user} />}</main><Toaster position="bottom-right" /></div>;
}
