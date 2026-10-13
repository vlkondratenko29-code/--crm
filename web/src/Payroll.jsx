import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

// Payroll: handlers enter their deposits, the admin approves them, the CRM computes salary.
const money = v => v == null ? "—" : "$" + Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 2 });
const who = h => (h.includes("@") ? h : "@" + h);
const day = d => new Date(d + "T00:00:00").toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
const STATUS = { pending: ["на проверке", ""], approved: ["подтверждён", "t-ftd"], rejected: ["отклонён", "t-bad"] };
const KINDS = [["FTD", "FTD"], ["REDEP", "Передеп"]];

function monthNow() {
  const d = new Date(Date.now() + 3 * 3600 * 1000);
  return d.toISOString().slice(0, 7);
}
function shiftMonth(m, delta) {
  const [y, mo] = m.split("-").map(Number);
  const d = new Date(Date.UTC(y, mo - 1 + delta, 1));
  return d.toISOString().slice(0, 7);
}
const monthLabel = m => {
  const name = new Date(m + "-01T00:00:00").toLocaleDateString("ru-RU", { month: "long" });
  return name[0].toUpperCase() + name.slice(1) + " " + m.slice(0, 4);
};
const today = () => new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 10);
const blank = () => ({ day: today(), client: "", amount: "", kind: "FTD", comment: "", handler: "" });

function Salary({ r, rules, canEdit, onExtra }) {
  const [video, setVideo] = useState(String(r.video_reviews));
  const [dialogs, setDialogs] = useState(r.dialogs !== r.dialogs_auto ? String(r.dialogs) : "");
  useEffect(() => { setVideo(String(r.video_reviews)); setDialogs(r.dialogs !== r.dialogs_auto ? String(r.dialogs) : ""); }, [r]);
  const weeksWon = r.weeks.filter(w => w.bonus).length;
  return (
    <div className="buy-row">
      <div className="buy-head">
        <div><b>{who(r.handler)}{r.best ? " 🏆" : ""}</b>
          <small>CR {r.cr == null ? "—" : r.cr + "%"} ({r.ftd} FTD / {r.dialogs} диалогов) → {r.rate}% с продаж</small></div>
        <b className="t-money">{money(r.total)}</b>
      </div>
      <div className="buy-grid">
        <span className="t-money">{money(r.sales)}<small>продажи</small></span>
        <span>{money(r.fix)}<small>фикс</small></span>
        <span>{money(r.percent_pay)}<small>{r.rate}% с продаж</small></span>
        <span>{money(r.video_pay)}<small>видеоотзывы · {r.video_reviews}</small></span>
        <span>{money(r.week_pay)}<small>недели {rules.week_qftd}+ qFTD · {weeksWon}</small></span>
        {r.best_pay ? <span className="t-ftd">{money(r.best_pay)}<small>лучший МОП</small></span> : null}
        <span>{r.redep}<small>передепы</small></span>
      </div>
      {r.weeks.length > 0 && <p className="recon-hint">Недели: {r.weeks.map(w => `${day(w.from)}–${day(w.to)}: ${w.qftd} qFTD${w.bonus ? " ✓" : ""}`).join(" · ")}</p>}
      {canEdit && <div className="pay-extra">
        <label>Видеоотзывов<input type="number" min="0" value={video} onChange={e => setVideo(e.target.value)} /></label>
        <label>Диалогов (вручную)<input type="number" min="0" value={dialogs} placeholder={"авто: " + r.dialogs_auto} onChange={e => setDialogs(e.target.value)} /></label>
        <button className="upload-btn" onClick={() => onExtra(r.handler, video, dialogs)}>Сохранить</button>
      </div>}
    </div>
  );
}

function Rules({ rules, onSave }) {
  const [v, setV] = useState(rules);
  useEffect(() => setV(rules), [rules]);
  const num = k => e => setV({ ...v, [k]: e.target.value });
  const tier = (i, k) => e => setV({ ...v, tiers: v.tiers.map((t, j) => j === i ? { ...t, [k]: e.target.value } : t) });
  return (
    <div className="watch-settings">
      <label>Фикс в месяц, $<input type="number" value={v.fix} onChange={num("fix")} /></label>
      <div className="section-title">% с продаж по CR (диалог → FTD за месяц)</div>
      {v.tiers.map((t, i) => <div className="pay-extra" key={i}>
        <label>CR от, %<input type="number" value={t.from} onChange={tier(i, "from")} /></label>
        <label>Процент<input type="number" value={t.rate} onChange={tier(i, "rate")} /></label>
      </div>)}
      <label>За видеоотзыв, $<input type="number" value={v.video_bonus} onChange={num("video_bonus")} /></label>
      <div className="pay-extra">
        <label>qFTD за неделю<input type="number" value={v.week_qftd} onChange={num("week_qftd")} /></label>
        <label>Бонус за неделю, $<input type="number" value={v.week_bonus} onChange={num("week_bonus")} /></label>
      </div>
      <label>qFTD — FTD от суммы, $ (0 — любой подтверждённый FTD)<input type="number" value={v.qftd_min} onChange={num("qftd_min")} /></label>
      <label>Лучшему МОПу месяца, $<input type="number" value={v.best_bonus} onChange={num("best_bonus")} /></label>
      <button className="upload-btn" onClick={() => onSave(v)}>Сохранить правила</button>
    </div>
  );
}

export default function Payroll({ username }) {
  const [month, setMonth] = useState(monthNow());
  const [view, setView] = useState("deposits");
  const [deps, setDeps] = useState(null);
  const [sum, setSum] = useState(null);
  const [filter, setFilter] = useState("");
  const [form, setForm] = useState(blank());
  const [editing, setEditing] = useState(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showRules, setShowRules] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([
        api(`/api/v1/payroll/deposits?month=${month}&handler=${encodeURIComponent(filter)}`, { username }),
        api(`/api/v1/payroll/summary?month=${month}`, { username }),
      ]);
      setDeps(d); setSum(s); setError("");
    } catch (e) { setError(e.message); }
  }, [month, filter, username]);
  useEffect(() => { load(); }, [load]);

  const admin = !!deps?.can_review;
  const set = k => e => setForm({ ...form, [k]: e.target.value });

  async function submit() {
    setBusy(true); setMsg("");
    try {
      const body = { ...form, amount: Number(String(form.amount).replace(",", ".")), handler: form.handler || null };
      if (editing) await api(`/api/v1/payroll/deposits/${editing}`, { username, method: "POST", body });
      else await api("/api/v1/payroll/deposits", { username, method: "POST", body });
      setMsg(editing ? "Исправлено, запись снова на проверке." : "Депозит добавлен. Он попадёт в ЗП после подтверждения админом.");
      setForm({ ...blank(), handler: form.handler }); setEditing(null);
      await load();
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  }
  async function review(id, status) {
    try { await api(`/api/v1/payroll/deposits/${id}/review`, { username, method: "POST", body: { status } }); await load(); }
    catch (e) { setMsg(e.message); }
  }
  async function remove(id) {
    if (!window.confirm("Удалить запись?")) return;
    try { await api(`/api/v1/payroll/deposits/${id}`, { username, method: "DELETE" }); await load(); }
    catch (e) { setMsg(e.message); }
  }
  function edit(r) {
    setEditing(r.id);
    setForm({ day: r.day, client: r.client, amount: String(r.amount), kind: r.kind, comment: r.comment || "", handler: r.handler });
    window.scrollTo?.(0, 0);
  }
  async function saveExtra(handler, video, dialogs) {
    try {
      await api("/api/v1/payroll/extras", { username, method: "POST", body: {
        handler, month, video_reviews: Number(video) || 0,
        dialogs_override: dialogs === "" ? null : Number(dialogs), clear_override: dialogs === "" } });
      setMsg("Сохранено."); await load();
    } catch (e) { setMsg(e.message); }
  }
  async function saveRules(v) {
    try {
      await api("/api/v1/settings/payroll", { username, method: "POST", body: v });
      setMsg("Правила сохранены."); setShowRules(false); await load();
    } catch (e) { setMsg(e.message); }
  }

  const mine = sum?.rows?.length === 1 && !team ? sum.rows[0] : null;
  return (
    <section className="role-dashboard stats-screen">
      <div className="role-hero"><div><p className="eyebrow">ДЕПОЗИТЫ И ЗП</p>
        <h2>{team ? "Депозиты обработчиков" : "Мои депозиты"}</h2>
        <p>{ro ? "Только просмотр: депы обработчиков и их ЗП. ЗП считается по депам, которые подтвердил админ." : admin ? "Обработчики вносят депы, ты подтверждаешь. ЗП считается только по подтверждённым." : "Вноси каждый деп сразу. В ЗП он попадёт после подтверждения админом."}</p></div><span>💵</span></div>

      <div className="period-switch month-switch">
        <button onClick={() => setMonth(shiftMonth(month, -1))}>←</button>
        <button className="selected">{monthLabel(month)}</button>
        <button onClick={() => setMonth(shiftMonth(month, 1))} disabled={month >= monthNow()}>→</button>
      </div>
      {error && <div className="team-message">{error}</div>}

      {sum && <div className="st-kpis">
        {mine ? <>
          <div><span>К выплате</span><b className="t-money">{money(mine.total)}</b><small>пока месяц идёт — предварительно</small></div>
          <div><span>Продажи</span><b>{money(mine.sales)}</b><small>{mine.ftd} FTD · {mine.redep} передепов</small></div>
          <div><span>CR диалог → деп</span><b className="t-ftd">{mine.cr == null ? "—" : mine.cr + "%"}</b><small>{mine.rate}% с продаж</small></div>
          <div><span>На проверке</span><b>{deps?.pending ?? 0}</b><small>ещё не в ЗП</small></div>
        </> : <>
          <div><span>Фонд ЗП</span><b className="t-money">{money(sum.total)}</b><small>{monthLabel(month)}</small></div>
          <div><span>На проверке</span><b className={sum.pending ? "t-bad" : ""}>{sum.pending}</b><small>ждут подтверждения</small></div>
        </>}
      </div>}

      <div className="st-tabs">
        <button className={view === "deposits" ? "selected" : ""} onClick={() => setView("deposits")}>Депозиты</button>
        <button className={view === "salary" ? "selected" : ""} onClick={() => setView("salary")}>{team ? "Зарплаты" : "Моя ЗП"}</button>
      </div>

      {view === "deposits" && <>
        {!ro && <div className="watch-settings">
          <b>{editing ? "Исправить депозит" : "Новый депозит"}</b>
          {admin && <label>Обработчик
            <select value={form.handler} onChange={set("handler")}><option value="">я сам</option>{(deps?.handlers || []).map(h => <option key={h} value={h}>{who(h)}</option>)}</select></label>}
          <div className="pay-extra">
            <label>Дата<input type="date" value={form.day} onChange={set("day")} /></label>
            <label>Тип<select value={form.kind} onChange={set("kind")}>{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
          </div>
          <label>Клиент (ник, UID или номер счёта)<input value={form.client} onChange={set("client")} placeholder="@client / UID 123456" /></label>
          <label>Сумма, $<input type="number" inputMode="decimal" min="0" value={form.amount} onChange={set("amount")} /></label>
          <label>Комментарий<input value={form.comment} onChange={set("comment")} placeholder="необязательно" /></label>
          <div className="pay-actions">
            <button className="upload-btn" disabled={busy || !form.client || !form.amount} onClick={submit}>{busy ? "Сохраняю…" : editing ? "Сохранить" : "Добавить"}</button>
            {editing && <button className="link-btn" onClick={() => { setEditing(null); setForm(blank()); }}>Отмена</button>}
          </div>
        </div>}
        {msg && <div className="team-message">{msg}</div>}

        {team && <div className="period-switch filter-switch">
          <button className={!filter ? "selected" : ""} onClick={() => setFilter("")}>Все</button>
          {(deps?.handlers || []).map(h => <button key={h} className={filter === h ? "selected" : ""} onClick={() => setFilter(h)}>{who(h)}</button>)}
        </div>}
        <div className="st-feed">
          {(deps?.rows || []).map(r => {
            const [label, tone] = STATUS[r.status] || [r.status, ""];
            const own = (r.status === "pending" && !ro) || admin;
            return <div className="st-event watch-event" key={r.id}>
              <span className={"st-badge " + (r.kind === "FTD" ? "t-ftd" : "t-reg")}>{r.kind === "FTD" ? "FTD" : "ПЕРЕДЕП"}</span>
              <div><b>{r.client} · <span className="t-money">{money(r.amount)}</span></b>
                <small>{day(r.day)}{team ? " · " + who(r.handler) : ""} · <span className={tone}>{label}</span>{r.comment ? " · " + r.comment : ""}</small>
                <div className="pay-actions">
                  {admin && r.status !== "approved" && <button className="mini-btn ok" onClick={() => review(r.id, "approved")}>Подтвердить</button>}
                  {admin && r.status !== "rejected" && <button className="mini-btn bad" onClick={() => review(r.id, "rejected")}>Отклонить</button>}
                  {own && <button className="mini-btn" onClick={() => edit(r)}>Изменить</button>}
                  {own && <button className="mini-btn" onClick={() => remove(r.id)}>Удалить</button>}
                </div></div>
            </div>;
          })}
          {deps && !deps.rows.length && <div className="empty-state">За {monthLabel(month)} депозитов нет.</div>}
        </div>
      </>}

      {view === "salary" && sum && <div className="st-list">
        {sum.rows.map(r => <Salary key={r.handler} r={r} rules={sum.rules} canEdit={sum.can_edit} onExtra={saveExtra} />)}
        {!sum.rows.length && <div className="empty-state">Нет обработчиков.</div>}
        {msg && <div className="team-message">{msg}</div>}
        <p className="recon-hint">CR = подтверждённые FTD за месяц / диалоги за месяц. Диалоги — клиенты, назначенные на обработчика в Chatterfy; если число неверное, админ вписывает его вручную.
          Ставка: {[...sum.rules.tiers].sort((a, b) => a.from - b.from).map(t => `от ${t.from}% → ${t.rate}%`).join(", ")}. Неделя (пн–вс) с {sum.rules.week_qftd}+ qFTD даёт {money(sum.rules.week_bonus)}; неделя идёт в месяц, где её воскресенье.</p>
        {sum.can_edit && <>
          <button className="link-btn" onClick={() => setShowRules(v => !v)}>{showRules ? "Скрыть правила" : "Правила расчёта"}</button>
          {showRules && <Rules rules={sum.rules} onSave={saveRules} />}
        </>}
      </div>}
      {!deps && !error && <p className="subtitle">Загружаю…</p>}
    </section>
  );
}
