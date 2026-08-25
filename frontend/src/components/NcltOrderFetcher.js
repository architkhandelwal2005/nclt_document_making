import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, ExternalLink, FileSearch, RefreshCw } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { toast } from "sonner";

const TERMINAL_ERRORS = new Set([
  "NCLT_SITE_UNAVAILABLE", "SEARCH_FORM_NOT_FOUND", "SEARCH_FAILED", "NO_CASE_FOUND",
  "CASE_DETAILS_FAILED", "PROCEEDINGS_NOT_FOUND", "ORDER_LINK_FAILED", "DOWNLOAD_FAILED",
  "INVALID_PDF", "SITE_STRUCTURE_CHANGED", "SESSION_EXPIRED", "BROWSER_SETUP_REQUIRED",
]);

function casePrefill(caseRecord) {
  const reference = String(caseRecord?.petition_number || "");
  const match = reference.match(/(\d+)\D+(\d{4})/);
  return {
    case_number: match?.[1] || "",
    case_year: match?.[2] || new Date().getFullYear(),
    bench: /indore/i.test(caseRecord?.nclt_bench || "") ? "indore" : "",
  };
}

function StatusBadge({ value }) {
  const css = value === "NEW" ? "new" : value === "ALREADY_DOWNLOADED" || value === "DOWNLOADED" ? "done" : value === "NO_ORDER" ? "muted" : "warn";
  return <span className={`fetch-status ${css}`}>{String(value || "UNKNOWN").replaceAll("_", " ")}</span>;
}

export default function NcltOrderFetcher({ caseRecord = null }) {
  const initial = useMemo(() => casePrefill(caseRecord), [caseRecord]);
  const [config, setConfig] = useState(null);
  const [form, setForm] = useState({ ...initial, case_type: "", case_type_label: "" });
  const [run, setRun] = useState(null);
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(false);

  const loadHistory = useCallback(async () => {
    try {
      const query = caseRecord ? `?case_id=${caseRecord.id}` : "";
      const { data } = await api.get(`/nclt-fetcher/runs${query}`);
      setHistory(data);
    } catch (error) {
      toast.error(errorMessage(error, "Could not load NCLT check history."));
    }
  }, [caseRecord]);

  useEffect(() => {
    api.get("/nclt-fetcher/config").then(({ data }) => {
      setConfig(data);
      setForm(current => ({
        ...current,
        bench: current.bench || data.default_bench,
        case_type: current.case_type || data.default_case_type,
        case_type_label: current.case_type_label || data.default_case_type_label,
      }));
    }).catch(error => toast.error(errorMessage(error, "Could not load NCLT fetcher configuration.")));
    loadHistory();
  }, [loadHistory]);

  const start = async event => {
    event.preventDefault(); setBusy(true);
    try {
      const payload = { ...form, case_year: Number(form.case_year), case_id: caseRecord?.id || null };
      const { data } = await api.post("/nclt-fetcher/runs", payload);
      setRun(data); await loadHistory();
      if (data.status === "MANUAL_ACTION_REQUIRED") toast.info("Chromium is ready. Complete the NCLT CAPTCHA, then return here and continue.", { duration: 12000 });
    } catch (error) {
      toast.error(errorMessage(error, "Could not start the NCLT case check."), { duration: 9000 });
    } finally { setBusy(false); }
  };

  const continueRun = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/nclt-fetcher/runs/${run.id}/continue`);
      setRun(data); await loadHistory();
      if (data.status === "MANUAL_ACTION_REQUIRED") toast.info("The CAPTCHA is still incomplete or was rejected. Complete it in Chromium and try again.");
    } catch (error) { toast.error(errorMessage(error, "Could not continue the NCLT check.")); }
    finally { setBusy(false); }
  };

  const selectCandidate = async candidateId => {
    setBusy(true);
    try {
      const { data } = await api.post(`/nclt-fetcher/runs/${run.id}/select`, { candidate_id: candidateId });
      setRun(data); await loadHistory();
    } catch (error) { toast.error(errorMessage(error, "Could not open the selected NCLT case.")); }
    finally { setBusy(false); }
  };

  const downloadNew = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/nclt-fetcher/runs/${run.id}/download`);
      setRun(data); await loadHistory();
      const summary = data.result?.download_summary;
      toast.success(`${summary?.downloaded || 0} new NCLT order(s) downloaded; ${summary?.skipped || 0} skipped.`);
    } catch (error) { toast.error(errorMessage(error, "Could not download the new NCLT orders.")); }
    finally { setBusy(false); }
  };

  const openRecord = async recordId => {
    try {
      const response = await api.get(`/nclt-fetcher/records/${recordId}/file`, { responseType: "blob" });
      const url = URL.createObjectURL(response.data); window.open(url, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) { toast.error(errorMessage(error, "The downloaded NCLT order file is unavailable.")); }
  };

  const result = run?.result || {};
  const summary = result.summary || {};
  const proceedings = result.proceedings || [];
  const recordById = Object.fromEntries((run?.records || []).map(item => [item.id, item]));
  const hasNew = (run?.records || []).some(item => ["NEW", "DOWNLOAD_FAILED"].includes(item.status));

  return <div className="space-y-6 nclt-fetcher" data-testid="nclt-order-fetcher">
    <section className="casefile-panel nclt-fetch-form">
      <div className="panel-heading"><div><p className="kicker">PUBLIC NCLT PORTAL</p><h3>NCLT Order Fetcher</h3></div><FileSearch size={22} /></div>
      <p className="module-description">Enter the case number and year. A visible Chromium window will open because the NCLT portal requires manual CAPTCHA verification.</p>
      <form className="form-grid" onSubmit={start}>
        <label>Case Number<input required value={form.case_number} onChange={event => setForm({ ...form, case_number: event.target.value })} placeholder="72" /></label>
        <label>Year<input required type="number" min="1990" max="2100" value={form.case_year} onChange={event => setForm({ ...form, case_year: event.target.value })} /></label>
        <label>Bench<select required value={form.bench} onChange={event => setForm({ ...form, bench: event.target.value })}>{(config?.benches || []).map(item => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
        <label>Case Type<select required value={form.case_type} onChange={event => { const option = config?.case_types.find(item => item.value === event.target.value); setForm({ ...form, case_type: event.target.value, case_type_label: option?.label || "" }); }}>{(config?.case_types || []).map(item => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
        <div className="full nclt-form-actions"><button className="primary-button" disabled={busy || !config}><FileSearch size={16} />{busy ? "Opening NCLT…" : "Fetch NCLT case"}</button>{run && <button type="button" className="outline-button" onClick={() => setRun(null)}><RefreshCw size={15} />New check</button>}</div>
      </form>
    </section>

    {run?.status === "MANUAL_ACTION_REQUIRED" && <section className="casefile-panel nclt-manual"><AlertTriangle size={24} /><div><h3>Manual action required</h3><p>{run.error_message}</p><ol><li>Switch to the opened Chromium window.</li><li>Enter the CAPTCHA shown by the NCLT website.</li><li>Return here and press Continue. The software will submit the verified search if required.</li></ol><button className="primary-button" disabled={busy} onClick={continueRun}>{busy ? "Checking…" : "Continue after manual verification"}</button></div></section>}

    {run?.status === "MULTIPLE_MATCHES_NEEDS_REVIEW" && <section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">REVIEW REQUIRED</p><h3>Multiple matching cases</h3></div><AlertTriangle /></div><div className="nclt-candidates">{(result.candidates || []).map(candidate => <article key={candidate.id}><div><b>{candidate.case_title}</b><small>{candidate.row_text}</small></div><button className="outline-button" disabled={busy} onClick={() => selectCandidate(candidate.id)}>Select this case</button></article>)}</div></section>}

    {run && TERMINAL_ERRORS.has(run.error_code) && <section className="casefile-panel nclt-error"><AlertTriangle /><div><b>{String(run.error_code).replaceAll("_", " ")}</b><p>{run.error_message}</p>{run.debug?.screenshot && <small>Debug evidence: {run.debug.screenshot}</small>}</div></section>}

    {result.case?.title && <section className="casefile-panel nclt-case-found"><div><p className="kicker">NCLT CASE FOUND</p><h3>{result.case.applicant || result.case.title}</h3>{result.case.respondent && <><span>versus</span><h3>{result.case.respondent}</h3></>}<p>{result.case.case_number || `${form.case_number}/${form.case_year}`} · {result.case.bench || "NCLT Indore Bench"}</p></div><CheckCircle2 size={28} /></section>}

    {proceedings.length > 0 && <section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">CASE PROCEEDING DETAILS</p><h3>Proceeding history</h3></div><div className="nclt-summary"><span><b>{summary.proceedings_found || 0}</b> proceedings</span><span><b>{summary.orders_found || 0}</b> orders</span><span><b>{summary.new_orders || 0}</b> new</span><span><b>{summary.already_downloaded || 0}</b> downloaded</span></div></div><div className="module-table-wrap"><table className="module-table nclt-table"><thead><tr><th>Date</th><th>Purpose</th><th>Next Date</th><th>Order</th><th>Download status</th><th></th></tr></thead><tbody>{proceedings.map((item, index) => { const record = recordById[item.record_id]; const status = record?.status || item.download_status; return <tr key={`${item.row_number}-${index}`}><td>{item.date || item.date_raw || "—"}</td><td>{item.purpose || "—"}</td><td>{item.next_date || item.next_date_raw || "—"}</td><td>{item.order_type || item.order_text || "No order"}</td><td><StatusBadge value={status} /></td><td>{record?.status === "DOWNLOADED" && <button className="icon-btn" onClick={() => openRecord(record.id)} title="Open downloaded order"><ExternalLink size={15} /></button>}</td></tr>; })}</tbody></table></div><div className="nclt-download-actions"><button className="primary-button" disabled={busy || !hasNew} onClick={downloadNew}><Download size={16} />{busy ? "Downloading…" : hasNew ? "Download all new orders" : "No new orders to download"}</button><button className="outline-button" disabled={busy} onClick={() => { setRun(null); }}><RefreshCw size={15} />Fetch / check again</button></div></section>}

    <section className="casefile-panel"><div className="panel-heading"><div><p className="kicker">RUN HISTORY</p><h3>Recent NCLT checks</h3></div><span className="status-chip">{history.length} runs</span></div><div className="nclt-history">{history.map(item => <button key={item.id} onClick={() => api.get(`/nclt-fetcher/runs/${item.id}`).then(({ data }) => setRun(data))}><span><b>{item.case_title || `Case ${item.case_number}/${item.case_year}`}</b><small>{new Date(item.created_at).toLocaleString()} · {item.bench} · {item.status.replaceAll("_", " ")}</small></span><StatusBadge value={item.status} /></button>)}{!history.length && <p className="panel-empty">No NCLT checks have been run yet.</p>}</div></section>
  </div>;
}
