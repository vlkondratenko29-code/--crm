import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

// Shift schedule: handlers mark their working hours for the week; gaps in coverage are highlighted.
const who = h => (h.includes("@") ? h : "@" + h);
const dayTitle = d => {
  const s = new Date(d + "T00:00:00").toLocaleDateString("ru-RU", { weekday: "short", day: "2-digit", month: "short" });
  return s[0].toUpperCase() + s.slice(1);
};
const addDays = (d, n) => { const x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };
const hour = t => Number(t.slice(0, 2)) + Number(t.slice(3)) / 60;

// Hours of `day` (0..24) covered by each shift, including night shifts that started the day before.
function coverage(day, rows) {
  const counts = Array(24).fill(0);
  const prev = addDays(day, -1);
  for (const r of rows) {
    const s = hour(r.start), e = hour(r.end);
    const overnight = e <= s;
    let from = null, to = null;
    if (r.day === day) { from = s; to = overnight ? 24 : e; }
    else if (r.day === prev && overnight && e > 0) { from = 0; to = e; }
    if (from == null) continue;
    for (let h = 0; h < 24; h++) if (h + 1 > from && h < to) counts[h] += 1;
  }
  return counts;
}

// Hours of the day that belong to a team shift (day 08–16, night 19–03); a gap only counts there.
function workHours(presets) {
  const on = Array(24).fill(false);
  for (const p of presets) {
    const s = hour(p.start), e = hour(p.end);
    for (let h = 0; h < 24; h++) if (e > s ? h >= s && h < e : h >= s || h < e) on[h] = true;
  }
  return on;
}

function Coverage({ counts, work }) {
  return (
    <div className="shift-cover" title="Покрытие по часам">
      {counts.map((c, h) => <i key={h} className={!work[h] && !c ? "off" : c === 0 ? "c0" : c === 1 ? "c1" : "c2"} title={`${h}:00 — на смене: ${c}`} />)}
    </div>
  );
}

export default function Shifts({ username }) {
  const [week, setWeek] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [openDay, setOpenDay] = useState(null);
  const [custom, setCustom] = useState({ start: "08:00", end: "16:00" });
  const [forWhom, setForWhom] = useState("");

  const load = useCallback(async () => {
    try { setData(await api("/api/v1/shifts" + (week ? "?week=" + week : ""), { username })); setError(""); }
    catch (e) { setError(e.message); }
  }, [week, username]);
  useEffect(() => { load(); }, [load]);

  async function add(day, start, end) {
    setMsg("");
    try {
      await api("/api/v1/shifts", { username, method: "POST", body: { day, start, end, handler: forWhom || null } });
      await load();
    } catch (e) { setMsg(e.message); }
  }
  async function remove(id) {
    try { await api(`/api/v1/shifts/${id}`, { username, method: "DELETE" }); await load(); }
    catch (e) { setMsg(e.message); }
  }

  const admin = !!data?.can_edit_all;
  const ro = !!data?.read_only;  // SEO: sees the schedule, changes nothing
  const team = admin || ro;
  const me = (data?.me || "").toLowerCase();
  const work = workHours(data?.presets || []);
  const holes = d => coverage(d, data.rows).filter((c, h) => work[h] && c === 0).length;
  const gaps = data ? data.days.filter(d => holes(d) > 0).length : 0;
  const myHours = data ? data.rows.filter(r => r.handler.toLowerCase() === me).reduce((a, r) => a + ((hour(r.end) - hour(r.start) + 24) % 24 || 24), 0) : 0;

  return (
    <section className="role-dashboard stats-screen">
      <div className="role-hero"><div><p className="eyebrow">ГРАФИК СМЕН</p>
        <h2>{team ? "Смены команды" : "Мои смены"}</h2>
        <p>{team ?"Кто и когда на смене. Красным — часы, когда никого нет." : "Отметь, когда работаешь на этой неделе. Время турецкое."}</p></div><span>🗓</span></div>

      <div className="period-switch month-switch">
        <button onClick={() => setWeek(addDays(data?.week || "", -7))} disabled={!data}>←</button>
        <button className="selected">{data ? `${dayTitle(data.days[0])} — ${dayTitle(data.days[6])}` : "…"}</button>
        <button onClick={() => setWeek(addDays(data?.week || "", 7))} disabled={!data}>→</button>
      </div>
      {error && <div className="team-message">{error}</div>}
      {data && <div className="st-kpis">
        {team ? <>
          <div><span>Смен на неделе</span><b>{data.rows.length}</b><small>МОПов: {new Set(data.rows.map(r => r.handler)).size} из {data.handlers.length}</small></div>
          <div><span>Дней с дырами</span><b className={gaps ? "t-bad" : "t-ftd"}>{gaps}</b><small>есть часы без МОПа</small></div>
        </> : <>
          <div><span>Мои часы</span><b>{myHours}</b><small>за неделю</small></div>
          <div><span>Моих смен</span><b>{data.rows.filter(r => r.handler.toLowerCase() === me).length}</b><small>за неделю</small></div>
        </>}
      </div>}
      {admin && data && <label className="shift-for">Ставлю смену за
        <select value={forWhom} onChange={e => setForWhom(e.target.value)}><option value="">себя</option>{data.handlers.map(h => <option key={h} value={h}>{who(h)}</option>)}</select></label>}
      {msg && <div className="team-message">{msg}</div>}

      {data && <div className="st-list">
        {data.days.map(d => {
          const rows = data.rows.filter(r => r.day === d);
          const counts = coverage(d, data.rows);
          const open = openDay === d;
          return <div className={"buy-row shift-day" + (d === data.today ? " today" : "")} key={d}>
            <div className="buy-head"><div><b>{dayTitle(d)}{d === data.today ? " · сегодня" : ""}</b>
              <small>{holes(d) ? `без МОПа: ${holes(d)} ч` : "обе смены закрыты"}</small></div>
              {!ro && <button className="mini-btn" onClick={() => setOpenDay(open ? null : d)}>{open ? "Готово" : "+ Смена"}</button>}</div>
            <Coverage counts={counts} work={work} />
            <div className="shift-list">
              {rows.map(r => {
                const mine = r.handler.toLowerCase() === me;
                return <span key={r.id} className={"shift-chip" + (mine ? " mine" : "")}>
                  {who(r.handler)} {r.start}–{r.end === "00:00" ? "24:00" : r.end}
                  {(mine || admin) && <button onClick={() => remove(r.id)} aria-label="Убрать смену">×</button>}
                </span>;
              })}
              {!rows.length && <small className="subtitle">Никто не отметился</small>}
            </div>
            {open && <div className="shift-add">
              <div className="pay-actions">{data.presets.map(p => <button key={p.label} className="mini-btn ok" onClick={() => add(d, p.start, p.end)}>{p.label} {p.start}–{p.end}</button>)}</div>
              <div className="pay-extra">
                <label>С<input type="time" value={custom.start} onChange={e => setCustom({ ...custom, start: e.target.value })} /></label>
                <label>До<input type="time" value={custom.end} onChange={e => setCustom({ ...custom, end: e.target.value })} /></label>
                <button className="upload-btn" onClick={() => add(d, custom.start, custom.end)}>Добавить</button>
              </div>
            </div>}
          </div>;
        })}
        <p className="recon-hint">Смены: дневная 08:00–16:00, ночная 19:00–03:00 (переходит на следующий день). Полоска — 24 часа: красный — смена никем не закрыта, жёлтый — один МОП, зелёный — двое и больше, серый — перерыв между сменами. Если нужно другое время, задай его вручную.</p>
      </div>}
      {!data && !error && <p className="subtitle">Загружаю…</p>}
    </section>
  );
}
