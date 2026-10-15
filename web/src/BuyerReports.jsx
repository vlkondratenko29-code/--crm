import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

// Buyer reports: buyers log daily spend per campaign; the CRM adds leads -> REG -> FTD and ROI.
const PERIODS = [["1", "Сегодня"], ["7", "7 дней"], ["30", "30 дней"], ["0", "Всё время"]];
const money = v => v == null ? "—" : "$" + Number(v).toLocaleString("ru-RU", { maximumFractionDigits: v < 10 ? 2 : 0 });
const pct = v => v == null ? "—" : v + "%";
const who = h => (h.includes("@") ? h : "@" + h);
const day = d => new Date(d + "T00:00:00").toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
const today = () => new Date(Date.now() + 3 * 3600 * 1000).toISOString().slice(0, 10);
const blank = () => ({ day: today(), campaign: "", spend: "", accounts: "", bans: "", comment: "" });
const roiTone = v => v == null ? "" : v >= 0 ? "t-ftd" : "t-bad";

function Nums({ r }) {
  return (
    <div className="buy-grid">
      <span className="t-money">{money(r.spend)}<small>расход</small></span>
      <span>{r.leads}<small>лиды</small></span>
      <span>{money(r.cpl)}<small>цена лида</small></span>
      <span className="t-reg">{r.reg}<small>реги</small></span>
      <span className="t-ftd">{r.ftd}<small>депы</small></span>
      <span>{money(r.cp_ftd)}<small>цена депа</small></span>
      <span className="t-ftd">{money(r.cpa)}<small>доход CPA</small></span>
      <span>{money(r.deposits)}<small>сумма депов</small></span>
      <span className={roiTone(r.roi)}>{pct(r.roi)}<small>ROI</small></span>
    </div>
  );
}

export default function BuyerReports({ username }) {
  const [days, setDays] = useState("7");
  const [view, setView] = useState("form");
  const [data, setData] = useState(null);
  const [form, setForm] = useState(blank());
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setData(await api(`/api/v1/buying/reports?days=${days}`, { username })); setError(""); }
    catch (e) { setError(e.message); }
  }, [days, username]);
  useEffect(() => { load(); }, [load]);

  const set = k => e => setForm({ ...form, [k]: e.target.value });
  const n = v => v === "" ? null : Number(String(v).replace(",", "."));

  async function submit() {
    setBusy(true); setMsg("");
    try {
      await api("/api/v1/buying/reports", { username, method: "POST", body: {
        day: form.day, campaign: form.campaign, spend: n(form.spend) ?? 0,
        accounts: n(form.accounts), bans: n(form.bans), comment: form.comment } });
      setMsg("Отчёт сохранён. Повторная запись за тот же день и кампанию заменяет старую.");
      setForm({ ...blank(), day: form.day });
      await load();
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  }
  async function remove(id) {
    if (!window.confirm("Удалить строку отчёта?")) return;
    try { await api(`/api/v1/buying/reports/${id}`, { username, method: "DELETE" }); await load(); }
    catch (e) { setMsg(e.message); }
  }

  const t = data?.total;
  const manager = data && data.viewer.role !== "buyer";
  const tabs = [["form", "Внести отчёт"], ["days", "По дням"], ...(manager ? [["buyers", "По баерам"]] : [])];
  return (
    <section className="role-dashboard stats-screen">
      <div className="role-hero"><div><p className="eyebrow">ОТЧЁТЫ БАИНГА</p>
        <h2>{manager ? "Отчётность баинга" : "Мои отчёты"}</h2>
        <p>Баер вносит расход по кампаниям за день, CRM добавляет лиды, реги, депы и ROI по этим кампаниям. ROI считается по доходу CPA (сетка FxPro), а не по сумме депов клиентов.</p></div><span>📊</span></div>
      <div className="period-switch">{PERIODS.map(([v, l]) => <button key={v} className={days === v ? "selected" : ""} onClick={() => setDays(v)}>{l}</button>)}</div>
      {error && <div className="team-message">{error}</div>}
      {t && <div className="st-kpis">
        <div><span>Расход</span><b className="t-money">{money(t.spend)}</b><small>по отчётам</small></div>
        <div><span>Лиды</span><b>{t.leads}</b><small>цена {money(t.cpl)}</small></div>
        <div><span>Депы</span><b className="t-ftd">{t.ftd}</b><small>цена {money(t.cp_ftd)}</small></div>
        <div><span>ROI</span><b className={roiTone(t.roi)}>{pct(t.roi)}</b><small>доход CPA {money(t.cpa)}</small></div>
      </div>}

      <div className="st-tabs">{tabs.map(([k, l]) => <button key={k} className={view === k ? "selected" : ""} onClick={() => setView(k)}>{l}</button>)}</div>

      {view === "form" && <>
        <div className="watch-settings">
          <div className="pay-extra">
            <label>Дата<input type="date" value={form.day} onChange={set("day")} /></label>
            <label>Расход, $<input type="number" inputMode="decimal" min="0" value={form.spend} onChange={set("spend")} /></label>
          </div>
          <label>Кампания / связка<input list="buy-campaigns" value={form.campaign} onChange={set("campaign")} placeholder="как в Ads Manager" /></label>
          <datalist id="buy-campaigns">{(data?.campaigns || []).map(c => <option key={c} value={c} />)}</datalist>
          <div className="pay-extra">
            <label>Аккаунтов в работе<input type="number" min="0" value={form.accounts} onChange={set("accounts")} /></label>
            <label>Банов за день<input type="number" min="0" value={form.bans} onChange={set("bans")} /></label>
          </div>
          <label>Комментарий<input value={form.comment} onChange={set("comment")} placeholder="что тестировал, что отключил" /></label>
          <button className="upload-btn" disabled={busy || !form.campaign || form.spend === ""} onClick={submit}>{busy ? "Сохраняю…" : "Сохранить отчёт"}</button>
        </div>
        {msg && <div className="team-message">{msg}</div>}
        <div className="section-title">Записи за период</div>
        <div className="st-feed">
          {(data?.rows || []).map(r => <div className="st-event watch-event" key={r.id}>
            <span className="st-badge">{day(r.day)}</span>
            <div><b>{r.campaign} · <span className="t-money">{money(r.spend)}</span></b>
              <small>{manager ? who(r.buyer) + " · " : ""}{r.accounts != null ? `акков ${r.accounts}` : ""}{r.bans != null ? ` · банов ${r.bans}` : ""}{r.comment ? " · " + r.comment : ""}</small>
              <div className="pay-actions"><button className="mini-btn" onClick={() => setForm({ day: r.day, campaign: r.campaign, spend: String(r.spend), accounts: r.accounts ?? "", bans: r.bans ?? "", comment: r.comment || "" })}>Изменить</button>
                <button className="mini-btn" onClick={() => remove(r.id)}>Удалить</button></div></div>
          </div>)}
          {data && !data.rows.length && <div className="empty-state">За период отчётов нет.</div>}
        </div>
      </>}

      {view === "days" && data && <div className="st-list">
        {data.daily.map(r => <div className="buy-row" key={r.date}>
          <div className="buy-head"><b>{day(r.date)}</b>{r.ads_spend != null && r.ads_spend > 0 ? <small>Ads Manager: {money(r.ads_spend)}</small> : null}</div>
          <Nums r={r} />
        </div>)}
        <p className="recon-hint">Расход — из отчётов баеров. Лиды, реги и депы — по дню события{manager ? " по всем кампаниям" : " по твоим кампаниям"}. Для сравнения показан расход из загруженного Ads Manager.</p>
      </div>}

      {view === "buyers" && data && <div className="st-list">
        {data.by_buyer.map(b => <div className="buy-row" key={b.buyer}>
          <div className="buy-head"><div><b>{who(b.buyer)}</b><small>кампаний {b.campaigns} · акков {b.accounts} · банов {b.bans}{b.accounts_cost ? ` · аккаунты ${money(b.accounts_cost)} в расходе` : ""}</small></div></div>
          <Nums r={b} />
        </div>)}
        {!data.by_buyer.length && <div className="empty-state">За период отчётов нет.</div>}
        <p className="recon-hint">Лиды, реги и депы баера — по кампаниям, которые он указал в отчётах за период. Если двое ведут одну кампанию, результат засчитается обоим.</p>
      </div>}
      {!data && !error && <p className="subtitle">Загружаю…</p>}
    </section>
  );
}
