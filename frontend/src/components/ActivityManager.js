import React, { useCallback, useEffect, useState } from "react";
import { History } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { toast } from "sonner";

export default function ActivityManager({ caseId }) {
  const [events, setEvents] = useState([]);
  const load = useCallback(async () => { try { setEvents((await api.get(`/cases/${caseId}/activity`)).data); } catch (error) { toast.error(errorMessage(error, "Could not load activity.")); } }, [caseId]);
  useEffect(() => { load(); }, [load]);
  return <section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">CHRONOLOGY</p><h3>Case activity</h3></div><History size={20} /></div><ol className="activity-list">{events.map(event => <li key={event.id}><time>{new Date(event.occurred_at).toLocaleString("en-IN")}</time><span><b>{event.title}</b><small>{event.entity_type} · {event.event_type}</small></span></li>)}{!events.length && <p className="panel-empty">No activity recorded.</p>}</ol></section>;
}
