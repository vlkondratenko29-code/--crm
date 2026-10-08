import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

const STAGES = ["LEAD", "REG", "FTD", "FT"];
const WORK_LABELS = {
  new: "New",
  in_progress: "In progress",
  callback: "Call back",
  no_answer: "No answer",
  won: "Done",
  lost: "Lost",
};
const MATCH_NAMES = { label: "label", email: "email", phone: "phone" };
const PERIODS = [["0", "All time"], ["1", "Today"], ["7", "7 days"], ["30", "30 days"]];
const PAGE = 50;

function shortDate(value) {
  if (!value) return "—";
  const d = new Date(value.length <= 10 ? value + "T00:00:00" : value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { day: "2-digit", month: "short" }) +
    (value.length > 10 ? " " + d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "");
}

function leadTitle(lead) {
  return lead.email || lead.name || (lead.tg_username ? "@" + lead.tg_username : "Chat " + lead.chat_ids?.[0]);
}

export default function Leads({ username, role }) {
  const [filters, setFilters] = useState({ q: "", stage: "", work_status: "", campaign: "", assignee: role === "handler" ? "me" : "", days: "0" });
  const [search, setSearch] = useState("");
  const [data, setData] = useState(null);
  const [rows, setRows] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);

  const load = useCallback(async (offset = 0) => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ ...filters, limit: String(PAGE), offset: String(offset) });
      const res = await api("/api/v1/leads?" + params.toString(), { username });
      setData(res);
      setRows(prev => (offset ? [...prev, ...res.rows] : res.rows));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [filters, username]);

  useEffect(() => { load(0); }, [load]);

  // Debounce the free-text search so we do not hit the API on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => setFilters(f => (f.q === search ? f : { ...f, q: search })), 350);
    return () => clearTimeout(t);
  }, [search]);

  const set = (key) => (e) => setFilters(f => ({ ...f, [key]: e.target ? e.target.value : e }));

  if (selected) {
    return <LeadCard leadKey={selected} username={username} canAssign={data?.can_assign} team={data?.team || []}
      onBack={() => { setSelected(null); load(0); }} />;
  }

  return (
    <section className="role-dashboard leads-dashboard">
      <div className="role-hero"><div><p className="eyebrow">LEADS · CHATTERFY</p><h2>Lead queue</h2><p>Every Chatterfy lead with its funnel stage and who is working on it.</p></div><span>🗂️</span></div>

      <div className="stage-chips">
        <button className={!filters.stage ? "selected" : ""} onClick={() => set("stage")("")}>All <b>{data ? Object.values(data.stage_counts || {}).reduce((a, b) => a + b, 0) : "…"}</b></button>
        {STAGES.map(s => <button key={s} className={filters.stage === s ? "selected" : ""} onClick={() => set("stage")(s)}>{s} <b>{data?.stage_counts?.[s] ?? "…"}</b></button>)}
      </div>

      <div className="leads-filters">
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search email, name, Click ID, campaign" />
        <select value={filters.assignee} onChange={set("assignee")}>
          <option value="">Everyone</option>
          <option value="me">Mine</option>
          <option value="none">Unassigned</option>
          {data?.can_assign && (data.team || []).map(m => <option key={m.username} value={m.username}>@{m.username}</option>)}
        </select>
        <select value={filters.work_status} onChange={set("work_status")}>
          <option value="">Any status</option>
          {Object.entries(WORK_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={filters.campaign} onChange={set("campaign")}>
          <option value="">All campaigns</option>
          {(data?.campaigns || []).map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={filters.days} onChange={set("days")}>
          {PERIODS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      </div>

      {error && <div className="team-message">{error}</div>}
      <div className="section-title">{data ? `${data.total} leads` : "Loading…"}</div>
      <div className="lead-list">
        {rows.map(lead => (
          <button className="lead-row" key={lead.lead_key} onClick={() => setSelected(lead.lead_key)}>
            <div className="lead-main">
              <b>{leadTitle(lead)}</b>
              <small>{[lead.campaign, lead.source].filter(Boolean).join(" · ") || "No attribution"}</small>
            </div>
            <div className="lead-side">
              <span className={"stage stage-" + lead.stage.toLowerCase()}>{lead.stage}{lead.ftd_amount != null ? " · $" + Number(lead.ftd_amount).toFixed(0) : ""}</span>
              <span className={"work work-" + lead.work_status}>{WORK_LABELS[lead.work_status] || lead.work_status}</span>
            </div>
            <div className="lead-meta">
              <span>{lead.assignee ? "@" + lead.assignee : "Unassigned"}</span>
              {lead.notes > 0 && <span>💬 {lead.notes}</span>}
              {lead.brokers?.length > 0 && <span title={"Matched by " + lead.broker_match}>{lead.brokers.join(", ")} ✓</span>}
              <span>{shortDate(lead.last_activity)}</span>
            </div>
          </button>
        ))}
        {data && !rows.length && !loading && <div className="empty-state">No leads match these filters.</div>}
      </div>
      {data && rows.length < data.total && <button className="load-more" disabled={loading} onClick={() => load(rows.length)}>{loading ? "Loading…" : `Show more (${data.total - rows.length})`}</button>}
    </section>
  );
}

function LeadCard({ leadKey, username, canAssign, team, onBack }) {
  const [lead, setLead] = useState(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const path = "/api/v1/leads/" + encodeURIComponent(leadKey);

  const load = useCallback(async () => {
    try { setLead(await api(path + "/detail", { username })); setError(""); }
    catch (e) { setError(e.message); }
  }, [path, username]);
  useEffect(() => { load(); }, [load]);

  async function updateWork(patch) {
    setBusy(true);
    try { await api(path + "/work", { username, method: "POST", body: patch }); await load(); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function addNote(e) {
    e.preventDefault();
    if (!note.trim()) return;
    setBusy(true);
    try { await api(path + "/notes", { username, method: "POST", body: { body: note } }); setNote(""); await load(); }
    catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  const me = (username || "").toLowerCase();
  const mine = lead?.assignee && lead.assignee.toLowerCase() === me;

  return (
    <section className="card lead-card">
      <button className="back-link" onClick={onBack}>← All leads</button>
      {error && <div className="team-message">{error}</div>}
      {!lead ? <p className="subtitle">Loading…</p> : <>
        <div className="card-title">
          <div><p className="eyebrow">LEAD</p><h2>{leadTitle(lead)}</h2></div>
          <span className={"stage stage-" + lead.stage.toLowerCase()}>{lead.stage}</span>
        </div>

        <div className="client-grid">
          <span>Name</span><b>{lead.name || "—"}</b>
          <span>Telegram</span><b>{lead.tg_username ? "@" + lead.tg_username : "—"}</b>
          <span>Campaign</span><b>{lead.campaign || "—"}</b>
          <span>Source</span><b>{lead.source || "—"}</b>
          <span>Click ID</span><b>{lead.click_id || "—"}</b>
          <span>First seen</span><b>{shortDate(lead.first_seen_at)}</b>
          <span>Phone</span><b>{lead.phone || "—"}</b>
          <span>Broker</span><b>{lead.brokers?.length ? `${lead.brokers.join(", ")} · by ${MATCH_NAMES[lead.broker_match] || lead.broker_match}` : "Not found yet"}</b>
          {lead.ftd_amount != null && <><span>FTD amount</span><b>${Number(lead.ftd_amount).toFixed(2)}</b></>}
        </div>
        {(lead.broker_accounts || []).length > 0 && <>
          <div className="section-title">Broker accounts</div>
          <div className="recent-list">{lead.broker_accounts.map((a, i) => <div key={a.broker + a.account_id + i}>
            <div><b>{a.broker} · #{a.account_id}</b><small>Reg {shortDate(a.registration_date)}{a.ftd_date ? " · FTD " + shortDate(a.ftd_date) : ""}{a.label ? " · label " + a.label : ""}</small></div>
            <span>{a.deposits != null ? "$" + Number(a.deposits).toFixed(0) : ""}</span>
          </div>)}</div>
        </>}
        {lead.chat_link && <a className="chat-link" href={lead.chat_link} target="_blank" rel="noreferrer">Open Chatterfy chat ↗</a>}

        <div className="section-title">Processing</div>
        <div className="work-buttons">
          {Object.entries(WORK_LABELS).map(([k, v]) => (
            <button key={k} disabled={busy} className={lead.work_status === k ? "selected work-" + k : ""} onClick={() => updateWork({ work_status: k })}>{v}</button>
          ))}
        </div>
        <div className="assign-row">
          <span>Owner: <b>{lead.assignee ? "@" + lead.assignee : "nobody"}</b></span>
          {canAssign ? (
            <select value={lead.assignee || ""} disabled={busy} onChange={e => updateWork({ assignee: e.target.value })}>
              <option value="">Unassigned</option>
              {team.map(m => <option key={m.username} value={m.username}>@{m.username}</option>)}
            </select>
          ) : !lead.assignee ? (
            <button className="primary" disabled={busy} onClick={() => updateWork({ assignee: me })}>Take lead</button>
          ) : mine ? (
            <button disabled={busy} onClick={() => updateWork({ assignee: "" })}>Release</button>
          ) : null}
        </div>

        <div className="section-title">Notes</div>
        <form className="note-form" onSubmit={addNote}>
          <input value={note} onChange={e => setNote(e.target.value)} placeholder="What happened? e.g. call back tomorrow 15:00" maxLength={2000} />
          <button className="primary" disabled={busy || !note.trim()}>Add</button>
        </form>
        <div className="note-list">
          {(lead.note_list || []).map(n => <div key={n.id}><p>{n.body}</p><small>@{n.author} · {shortDate(n.created_at)}</small></div>)}
          {!(lead.note_list || []).length && <div className="empty-state">No notes yet.</div>}
        </div>

        <div className="section-title">Client Journey</div>
        <div className="timeline">
          <div><b>LEAD</b><span>{shortDate(lead.first_seen_at)}</span></div>
          {(lead.events || []).map((ev, i) => <div key={ev.type + i}><b>{ev.type}</b><span>{shortDate(ev.date)}{ev.amount != null ? " · $" + ev.amount : ""}</span></div>)}
        </div>
      </>}
    </section>
  );
}
