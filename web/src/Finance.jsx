import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

// P&L and cohorts: income (CPA + IB) minus traffic, salaries and costs entered by hand; cohorts by lead month.
const money = v => v == null ? "—" : (v < 0 ? "−$" : "$") + Math.abs(Number(v)).toLocaleString("ru-RU", { maximumFractionDigits: 0 });
const pct = v => v == null ? "—" : v + "%";
const tone = v => v == null ? "" : v >= 0 ? "t-ftd" : "t-bad";
const PERIODS = [["3", "3 мес"], ["6", "6 мес"], ["12", "12 мес"]];
const MATRIX = [["roi_cum", "ROI по CPA"], ["ftd_pct_cum", "Лид → FTD"], ["deposits_cum", "Депозиты"]];

function monthNow() { return new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 7); }
function shiftMonth(m, delta) {
  const [y, mo] = m.split("-").map(Number);
  return new Date(Date.UTC(y, mo - 1 + delta, 1)).toISOString().slice(0, 7);
}
const monthLabel = (m, short) => {
  const name = new Date(m + "-01T00:00:00").toLocaleDateString("ru-RU", { month: short ? "short" : "long" });
  return name[0].toUpperCase() + name.slice(1) + " " + (short ? m.slice(2, 4) : m.slice(0, 4));
};

function MonthPnl({ m }) {
  const lines = (obj) => Object.entries(obj).filter(([, v]) => v);
  return (
    <div className="buy-row">
      <div className="buy-head">
        <div><b>{monthLabel(m.month)}</b><small>{m.leads} лидов · {m.ftd} FTD{m.cpa_source === "fact" ? ` · CPA расчёт ${money(m.cpa_calc)}` : ""}</small></div>
        <b className={tone(m.profit)}>{money(m.profit)}</b>
      </div>
      <div className="buy-grid">
        <span className="t-ftd">{money(m.income_total)}<small>доход</small></span>
        <span className="t-bad">{money(m.expense_total)}<small>расход</small></span>
        <span className={tone(m.roi)}>{pct(m.roi)}<small>ROI</small></span>
        <span>{pct(m.margin)}<small>маржа</small></span>
        <span>{money(m.cost_per_ftd)}<small>трафик на FTD</small></span>
      </div>
      <div className="pnl-lines">
        <div>{lines(m.income).map(([k, v]) => <p key={k}><span>{k}{k === "CPA" ? (m.cpa_source === "fact" ? " · факт" : " · расчёт") : ""}</span><b>{money(v)}</b></p>)}
          {!lines(m.income).length && <p><span>Доходов нет</span></p>}</div>
        <div>{lines(m.expense).map(([k, v]) => <p key={k}><span>{k}{k === "Трафик" && m.traffic_auto ? " · из отчётов" : ""}{k === "ЗП" && m.payroll_auto ? " · с ЗП обработчиков" : ""}</span><b>{money(v)}</b></p>)}
          {!lines(m.expense).length && <p><span>Расходов нет</span></p>}</div>
      </div>
    </div>
  );
}

function Cohorts({ data }) {
  const [metric, setMetric] = useState("roi_cum");
  const fmt = v => v == null ? "" : metric === "deposits_cum" ? money(v) : v + "%";
  const cell = v => v == null ? "" : metric === "deposits_cum" ? (v > 0 ? "t-ftd" : "") : metric === "roi_cum" ? tone(v) : "";
  const rows = data.cohorts.filter(c => c.leads);
  const cols = Math.max(1, ...rows.map(c => c[metric].filter(v => v != null).length));
  return <>
    <div className="st-list pnl-grid">
      {rows.slice().reverse().map(c => <div className="buy-row" key={c.month}>
        <div className="buy-head"><div><b>Когорта {monthLabel(c.month)}</b><small>{c.leads} лидов · {c.reg} REG · {c.ftd} FTD ({pct(c.lead_to_ftd)})</small></div>
          <b className={tone(c.roi)}>{pct(c.roi)}</b></div>
        <div className="buy-grid">
          <span className="t-money">{money(c.spend)}<small>трафик месяца</small></span>
          <span>{money(c.cpl)}<small>цена лида</small></span>
          <span>{money(c.cost_per_ftd)}<small>цена FTD</small></span>
          <span className="t-ftd">{money(c.cpa_revenue)}<small>CPA (расчёт)</small></span>
          <span>{money(c.deposits)}<small>депозиты</small></span>
        </div>
      </div>)}
      {!rows.length && <div className="empty-state">Лидов за период нет.</div>}
    </div>
    {rows.length > 0 && <>
      <div className="section-title">Как когорта набирает результат по месяцам жизни</div>
      <div className="period-switch filter-switch">{MATRIX.map(([k, l]) => <button key={k} className={metric === k ? "selected" : ""} onClick={() => setMetric(k)}>{l}</button>)}</div>
      <div className="cohort-scroll">
        <table className="cohort-table">
          <thead><tr><th>Когорта</th><th>Лиды</th>{Array.from({ length: cols }, (_, i) => <th key={i}>M{i}</th>)}</tr></thead>
          <tbody>{rows.map(c => <tr key={c.month}>
            <th>{monthLabel(c.month, true)}</th><td>{c.leads}</td>
            {c[metric].slice(0, cols).map((v, i) => <td key={i} className={cell(v)}>{fmt(v)}</td>)}
          </tr>)}</tbody>
        </table>
      </div>
      <p className="recon-hint">M0 — месяц, когда пришёл лид, M1 — следующий и т.д. Значения накопительные. ROI по CPA = (CPA по сетке за FTD когорты − трафик месяца) / трафик.
        Депозиты по месяцам считаются по разнице между загрузками отчёта FxPro, поэтому загружай отчёт 1-го числа каждого месяца — он закроет прошлый месяц. IB в когорты не входит: FxPro не даёт комиссию по клиентам.</p>
    </>}
  </>;
}

const TIERS = [["tier1", "Tier 1"], ["tier2", "Tier 2"], ["tier3", "Tier 3"]];
const bracketLabel = (b, i) => i + 1 < b.length ? `$${b[i]}–${b[i + 1]}` : `$${b[i]}+`;

function CpaRules({ username, rules, countries, onChanged }) {
  const [v, setV] = useState(rules);
  const [msg, setMsg] = useState("");
  useEffect(() => setV(rules), [rules]);
  if (!v) return null;
  const setTier = (country, tier) => setV({ ...v, country_tiers: { ...v.country_tiers, [country]: tier } });
  const known = countries.map(c => c.country);
  async function save() {
    setMsg("");
    try {
      await api("/api/v1/settings/finance", { username, method: "POST", body: {
        country_tiers: v.country_tiers, default_tier: v.default_tier, ib_per_lot: Number(v.ib_per_lot) || 0 } });
      setMsg("Условия сохранены."); onChanged();
    } catch (e) { setMsg(e.message); }
  }
  return <>
    <div className="section-title">Сетка CPA по сумме депозита</div>
    <div className="cohort-scroll">
      <table className="cohort-table">
        <thead><tr><th>Депозит</th>{TIERS.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead>
        <tbody>{v.cpa_brackets.map((_, i) => <tr key={i}><th>{bracketLabel(v.cpa_brackets, i)}</th>
          {TIERS.map(([k]) => <td key={k}>{money(v.cpa_tiers[k]?.[i])}</td>)}</tr>)}</tbody>
      </table>
    </div>
    <div className="section-title">Тир по странам</div>
    <div className="watch-settings">
      {known.map(c => <div className="pay-extra" key={c}>
        <span className="tier-country">{c}{countries.find(x => x.country === c) ? <small> · {countries.find(x => x.country === c).ftd} FTD</small> : null}</span>
        <label><select value={v.country_tiers[c] || ""} onChange={e => setTier(c, e.target.value)}>
          <option value="">как остальные</option>{TIERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      </div>)}
      {!known.length && <p className="recon-hint">Страны появятся, когда у FTD будут счета из отчёта FxPro.</p>}
      <div className="pay-extra">
        <label>Остальные страны<select value={v.default_tier} onChange={e => setV({ ...v, default_tier: e.target.value })}>
          {TIERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label>IB за лот, $<input type="number" min="0" value={v.ib_per_lot} onChange={e => setV({ ...v, ib_per_lot: e.target.value })} /></label>
      </div>
      <button className="upload-btn" onClick={save}>Сохранить условия</button>
      <p className="recon-hint">CPA клиента = ячейка сетки по тиру его страны (из отчёта FxPro) и сумме всех его депозитов за календарный месяц FTD (FTD + передепы того же месяца). Турция уже стоит в Tier 1.</p>
    </div>
    {msg && <div className="team-message">{msg}</div>}
  </>;
}

function Entries({ username, rules, countries, geos, onChanged }) {
  const [month, setMonth] = useState(monthNow());
  const [data, setData] = useState(null);
  const [form, setForm] = useState({ kind: "expense", category: "CRM", amount: "", comment: "", geo: "" });
  const [msg, setMsg] = useState("");
  const isIb = form.kind === "income" && form.category === "IB";

  const load = useCallback(async () => {
    try { setData(await api(`/api/v1/finance/entries?month=${month}`, { username })); } catch (e) { setMsg(e.message); }
  }, [month, username]);
  useEffect(() => { load(); }, [load]);

  const cats = data?.categories?.[form.kind] || [];
  async function add() {
    setMsg("");
    try {
      const n = Number(String(form.amount).replace(",", "."));
      await api("/api/v1/finance/entries", { username, method: "POST", body: { month, kind: form.kind, category: form.category, comment: form.comment, geo: form.geo || null, ...(isIb ? { lots: n } : { amount: n }) } });
      setForm({ ...form, amount: "", comment: "" }); await load(); onChanged();
    } catch (e) { setMsg(e.message); }
  }
  async function remove(id) {
    if (!window.confirm("Удалить запись?")) return;
    try { await api(`/api/v1/finance/entries/${id}`, { username, method: "DELETE" }); await load(); onChanged(); } catch (e) { setMsg(e.message); }
  }

  return <>
    <div className="period-switch month-switch">
      <button onClick={() => setMonth(shiftMonth(month, -1))}>←</button>
      <button className="selected">{monthLabel(month)}</button>
      <button onClick={() => setMonth(shiftMonth(month, 1))} disabled={month >= monthNow()}>→</button>
    </div>
    <div className="watch-settings">
      <div className="pay-extra">
        <label>Тип<select value={form.kind} onChange={e => { const kind = e.target.value; setForm({ ...form, kind, category: (data?.categories?.[kind] || [""])[0] }); }}>
          <option value="expense">Расход</option><option value="income">Доход</option></select></label>
        <label>Статья<select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })}>
          {cats.map(c => <option key={c} value={c}>{c}</option>)}</select></label>
      </div>
      <div className="pay-extra">
        <label>{isIb ? `Лоты (× $${rules?.ib_per_lot ?? 16})` : "Сумма, $"}<input type="number" inputMode="decimal" min="0" value={form.amount} onChange={e => setForm({ ...form, amount: e.target.value })} /></label>
        <label>Комментарий<input value={form.comment} onChange={e => setForm({ ...form, comment: e.target.value })} placeholder="за что" /></label>
      </div>
      {geos.length > 1 && <label>ГЕО<select value={form.geo} onChange={e => setForm({ ...form, geo: e.target.value })}>
        <option value="">Весь проект</option>{geos.map(g => <option key={g} value={g}>{g}</option>)}</select></label>}
      <button className="upload-btn" disabled={!form.amount || !data} onClick={add}>Добавить в {monthLabel(month)}</button>
      <p className="recon-hint">Трафик из отчётов баеров / Ads Manager и ЗП обработчиков считаются сами. Здесь вносится остальное: IB, CRM, накрутки, сервисы, аккаунты, ЗП команды.
        Если внести «CPA» как доход, за этот месяц берётся факт выплаты вместо расчёта.</p>
    </div>
    {msg && <div className="team-message">{msg}</div>}
    <div className="section-title">Записи за {monthLabel(month)}</div>
    <div className="st-feed">
      {(data?.rows || []).map(r => <div className="st-event watch-event" key={r.id}>
        <span className="st-badge">{r.kind === "income" ? "доход" : "расход"}</span>
        <div><b>{r.category} · <span className={r.kind === "income" ? "t-ftd" : "t-bad"}>{money(r.amount)}</span></b>
          <small>{r.geo ? `${r.geo} · ` : ""}{r.lots ? `${r.lots} лотов · ` : ""}{r.comment || ""}{r.created_by ? " · @" + r.created_by : ""}</small>
          <div className="pay-actions"><button className="mini-btn" onClick={() => remove(r.id)}>Удалить</button></div></div>
      </div>)}
      {data && !data.rows.length && <div className="empty-state">За этот месяц записей нет.</div>}
    </div>
    <CpaRules username={username} rules={rules} countries={countries || []} onChanged={onChanged} />
  </>;
}

export default function Finance({ username }) {
  const [view, setView] = useState("pnl");
  const [months, setMonths] = useState("6");
  const [geo, setGeo] = useState("");
  const [pnl, setPnl] = useState(null);
  const [cohorts, setCohorts] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [p, c] = await Promise.all([
        api(`/api/v1/pnl?months=${months}&geo=${geo}`, { username, timeout: 90000 }),
        api(`/api/v1/cohorts?months=${months}&geo=${geo}`, { username, timeout: 90000 }),
      ]);
      setPnl(p); setCohorts(c);
    } catch (e) { setError(e.message); }
  }, [months, geo, username]);
  useEffect(() => { load(); }, [load]);

  const t = pnl?.total;
  return (
    <section className="role-dashboard stats-screen">
      <div className="role-hero"><div><p className="eyebrow">P&L · КОГОРТЫ</p><h2>Прибыль проекта</h2>
        <p>CPA и IB минус трафик, ЗП и остальные расходы. Когорты по месяцу прихода лида.</p></div><span>📒</span></div>
      <div className="period-switch">{PERIODS.map(([v, l]) => <button key={v} className={months === v ? "selected" : ""} onClick={() => setMonths(v)}>{l}</button>)}</div>
      {pnl?.geos?.length > 1 && <div className="period-switch filter-switch">
        {[["", "Все ГЕО"], ...pnl.geos.map(g => [g, g])].map(([v, l]) => <button key={v} className={geo === v ? "selected" : ""} onClick={() => setGeo(v)}>{l}</button>)}</div>}
      {geo && <p className="recon-hint">Только {geo}: лиды бота {geo}, трафик его кампаний и записи с пометкой {geo}. ЗП и общие расходы проекта — в «Все ГЕО».</p>}
      {error && <div className="team-message">{error} <button className="link-btn" onClick={load}>Повторить</button></div>}
      {t && <div className="st-kpis">
        <div><span>Прибыль</span><b className={tone(t.profit)}>{money(t.profit)}</b><small>за период</small></div>
        <div><span>Доход</span><b className="t-ftd">{money(t.income)}</b><small>CPA + IB + другое</small></div>
        <div><span>Расход</span><b className="t-bad">{money(t.expense)}</b><small>все статьи</small></div>
        <div><span>ROI</span><b className={tone(t.roi)}>{pct(t.roi)}</b><small>{t.ftd} FTD</small></div>
      </div>}
      <div className="st-tabs">{[["pnl", "По месяцам"], ["cohorts", "Когорты"], ["entries", "Внести"]].map(([k, l]) =>
        <button key={k} className={view === k ? "selected" : ""} onClick={() => setView(k)}>{l}</button>)}</div>
      {view === "pnl" && pnl && <div className="st-list pnl-grid">{pnl.months.slice().reverse().map(m => <MonthPnl key={m.month} m={m} />)}
        <p className="recon-hint">CPA (расчёт) — по месяцу FTD, по сетке FxPro: тир страны × сумма депозитов клиента за месяц FTD. IB — лоты × ставка, вносится во вкладке «Внести». Трафик за день берётся по большему из двух источников: отчёты баеров или Ads Manager, чтобы не считать дважды.</p></div>}
      {view === "cohorts" && cohorts && <Cohorts data={cohorts} />}
      {view === "entries" && <Entries username={username} rules={pnl?.rules} countries={pnl?.countries} geos={pnl?.geos || []} onChanged={load} />}
      {!pnl && !error && view !== "entries" && <p className="subtitle">Считаю… первый раз может занять до минуты.</p>}
    </section>
  );
}
