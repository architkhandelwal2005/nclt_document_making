import React, { useEffect, useState } from "react";
import { CalendarDays } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { toast } from "sonner";

export default function CalendarView() {
  const [items, setItems] = useState([]);
  useEffect(() => { api.get("/firm/calendar").then(({ data }) => setItems(data)).catch(error => toast.error(errorMessage(error, "Could not load calendar."))); }, []);
  const grouped = items.reduce((result, item) => { const key = String(item.item_date || "").slice(0, 10) || "Unscheduled"; (result[key] ||= []).push(item); return result; }, {});
  return <section className="workspace"><div className="workspace-intro"><div><p className="kicker">CONSOLIDATED DIARY</p><h2>Firm Calendar</h2><p className="intro-copy">Hearings, CoC meetings, statutory deadlines, and task dates.</p></div><CalendarDays size={28} /></div><div className="calendar-list">{Object.entries(grouped).map(([day, entries]) => <section className="casefile-panel" key={day}><div className="calendar-day"><time>{day === "Unscheduled" ? day : new Date(`${day}T00:00:00`).toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "long", year: "numeric" })}</time><span>{entries.length} items</span></div>{entries.map(entry => <div className="calendar-entry" key={`${entry.item_type}-${entry.id}`}><span className="status-chip">{entry.item_type}</span><b>{entry.title || "Untitled"}</b><small>{entry.case_name} · {entry.status}</small></div>)}</section>)}{!items.length && <div className="casefile-panel panel-empty">No scheduled records.</div>}</div></section>;
}

