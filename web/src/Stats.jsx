import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import Buying from "./Buying.jsx";

// One statistics screen for every role. The backend scopes the data:
// handlers get only their own clients, SEO gets counts without money.
const PERIODS = [["1", "Сегодня"], ["7", "7 дней"], ["30", "30 дней"], ["0", "Всё время"]];
const TABS = [["buying", "Баинг"], ["handlers", "Обработчики"], ["campaigns", "Кампании"], ["feed", "Реги и депы"]];

const money = v => v == null ? "—" : "$" + Number(v).toLocaleString("ru-RU", { maximumFractionDigits: 0 });
const who = h => (h.includes("@") ? h : "@" + h);
const day = d => new Date(d + "T00:00:00").toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });

function Daily({ rows, showMoney }) {
  const max = Math.max(1, ...rows.map(r => Math.max(r.leads, r.reg, r.ftd)));
  return (
    <div className="st-daily">
      {rows.slice(-14).reverse().map(r => (
        <div className="st-day" key={r.date}>
          <span className="st-date">{day(r.date)}</span>
          <div className="st-bars">
            <i className="b-lead" style={{ width: r.leads / max * 100 + "%" }} />
            <i className="b-reg" style={{ width: r.reg / max * 100 + "%" }} />
            <i className="b-ftd" style={{ width: r.ftd / max * 100 + "%" }} />
          </div>
          <span className="st-nums"><b>{r.leads}</b> · <b className="t-reg">{r.reg}</b> · <b className="t-ftd">{r.ftd}</b>{showMoney && r.deposits ? <em> {money(r.deposits)}</em> : null}</span>
        </div>
      ))}
    </div>
  );
}

function Row({ title, sub, r, showMoney }) {
  return (
    <div className="st-row">
      <div className="st-row-main"><b>{title}</b>{sub && <small>{sub}</small>}</div>
      <div className="st-row-nums">
        <span>{r.leads ?? "—"}<small>лиды</small></span>
        <span className="t-reg">{r.reg}<small>REG</small></span>
        <span className="t-ftd">{r.ftd}<small>FTD</small></span>
        {showMoney && <span className="t-money">{money(r.deposits)}<small>депы</small></span>}
      </div>
    </div>
  );
}

// Speed to lead: minutes from the client's first message to the first answer of a live operator.
function ResponseSpeed({ username, days }) {
  const [d, setD] = useState(null);
  useEffect(() => { api(`/api/v1/stats/response?days=${days === "0" ? 30 : days}`, { username }).then(setD).catch(() => setD(null)); }, [username, days]);
  if (!d) return null;
  const tone = m => m == null ? "" : m <= 5 ? "t-ftd" : m <= 15 ? "t-money" : "t-bad";
  const mins = m => m == null ? "—" : m < 60 ? `${Math.round(m)} мин` : `${(m / 60).toFixed(1)} ч`;
  return <>
    <div className="section-title">Скорость первого ответа{d.median != null ? <> · медиана <span className={tone(d.median)}>{mins(d.median)}</span></> : null}</div>
    <div className="st-list">
      {d.rows.map(r => <div className="buy-row" key={r.operator}>
        <div className="buy-head"><b>{r.operator}</b><b className={tone(r.median)}>{mins(r.median)}</b></div>
        <div className="buy-grid">
          <span>{r.answered}<small>новых чатов</small></span>
          <span className={r.within5 >= 70 ? "t-ftd" : "t-bad"}>{r.within5}%<small>ответ до 5 мин</small></span>
          <span className={r.slow ? "t-bad" : ""}>{r.slow}<small>дольше 30 мин</small></span>
        </div>
      </div>)}
      {!d.rows.length && <div className="empty-state">Пока нет данных. Скорость считается по новым чатам, когда в «Контроле» указан ключ Chatterfy.</div>}
    </div>
    {d.unanswered > 0 && <p className="recon-hint">Без ответа оператора: {d.unanswered} новых чатов за период.</p>}
    {d.waiting.length > 0 && <>
      <div className="section-title">Ждут ответа сейчас ({d.waiting.length})</div>
      <div className="st-feed">{d.waiting.map(w => <div className="st-event" key={w.chat_id}>
        <span className={"st-badge " + (w.minutes > 30 ? "t-bad" : "t-money")}>{mins(w.minutes)}</span>
        <div><b>{w.name || "Клиент"}</b><small><a href={w.link} target="_blank" rel="noreferrer">открыть чат в Chatterfy</a></small></div>
      </div>)}</div>
    </>}
    <p className="recon-hint">Считается только живой оператор: приветствие бота и AI не в счёт. Ночью без смены время растёт — смотри медиану и «до 5 мин». Обновляется вместе с «Контролем» раз в 3 минуты.</p>
  </>;
}

export default function Stats({ username }) {
  const [days, setDays] = useState("7");
  const [tab, setTab] = useState("buying");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setData(null);
    try { setData(await api("/api/v1/stats/overview?days=" + days, { username })); setError(""); }
    catch (e) { setError(e.message); }
  }, [days, username]);
  useEffect(() => { load(); }, [load]);

  const s = data?.summary;
  const money_ = data?.show_money;
  const mine = data?.scope === "mine";
  const canBuy = data?.role === "admin" || data?.role === "head_buying";
  const tabs = TABS.filter(([k]) => !(mine && k === "handlers") && (k !== "buying" || canBuy) && !(canBuy && k === "campaigns"));
  const active = tabs.some(([k]) => k === tab) ? tab : tabs[0][0];

  return (
    <section className="role-dashboard stats-screen">
      <div className="role-hero"><div><p className="eyebrow">СТАТИСТИКА</p>
        <h2>{mine ? "Мои клиенты" : "Лиды → реги → депы"}</h2>
        <p>{mine ? "Твои реги и депозиты. Клиенты закрепляются за тобой в Chatterfy." : "Все отделы и обработчики. Данные из Chatterfy и отчётов FxPro."}</p></div><span>📈</span></div>
      <div className="period-switch">{PERIODS.map(([v, l]) => <button key={v} className={days === v ? "selected" : ""} onClick={() => setDays(v)}>{l}</button>)}</div>
      {error && <div className="team-message">{error}</div>}
      {!data && !error && <p className="subtitle">Считаю…</p>}
      {s && <>
        <div className="st-kpis">
          <div><span>Лиды</span><b>{s.leads}</b></div>
          <div><span>Реги</span><b className="t-reg">{s.reg}</b><small>{s.lead_to_reg}% от лидов</small></div>
          <div><span>Депы (FTD)</span><b className="t-ftd">{s.ftd}</b><small>{s.reg_to_ftd}% от рег</small></div>
          {money_ && <div><span>Сумма депов</span><b className="t-money">{money(s.deposits)}</b><small>средний {money(s.avg_ftd)}</small></div>}
        </div>

        <div className="section-title">По дням <span className="st-legend"><i className="b-lead" />лиды <i className="b-reg" />реги <i className="b-ftd" />депы</span></div>
        <Daily rows={data.daily} showMoney={money_} />

        <div className="st-tabs">{tabs.map(([k, l]) => <button key={k} className={active === k ? "selected" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>

        {active === "buying" && <Buying username={username} days={days} />}
        {mine && <ResponseSpeed username={username} days={days} />}

        {active === "handlers" && <ResponseSpeed username={username} days={days} />}
        {active === "handlers" && <div className="st-list">
          {data.by_handler.map(h => <Row key={h.handler || "none"} title={h.handler ? who(h.handler) : "Без обработчика"} r={{ ...h, leads: null }}
            sub={h.reg ? `${h.reg_to_ftd}% рег → деп` : ""} showMoney={money_} />)}
          {!data.by_handler.length && <div className="empty-state">За период рег и депов нет.</div>}
          <p className="recon-hint">Обработчик берётся из Chatterfy: на кого назначен чат. Чтобы видеть имя, а не email, укажи email Chatterfy у сотрудника во вкладке «Команда».</p>
        </div>}

        {active === "campaigns" && <div className="st-list">
          {data.by_campaign.map(c => <Row key={c.campaign} title={c.campaign} r={c} sub={c.leads ? `${c.lead_to_reg}% лид → рег` : ""} showMoney={money_} />)}
          {!data.by_campaign.length && <div className="empty-state">Нет данных за период.</div>}
        </div>}

        {active === "feed" && <div className="st-feed">
          {data.feed.map((e, i) => <div className="st-event" key={e.lead_key + e.type + i}>
            <span className={"st-badge " + (e.type === "FTD" ? "t-ftd" : "t-reg")}>{e.type === "FTD" ? "ДЕП" : "РЕГ"}</span>
            <div><b>{e.name || (e.tg_username ? "@" + e.tg_username : "Клиент")}</b>
              <small>{day(e.day)}{e.handler ? " · " + who(e.handler) : ""}{e.campaign ? " · " + e.campaign : ""}</small></div>
            {e.amount != null && <span className="t-money">{money(e.amount)}</span>}
          </div>)}
          {!data.feed.length && <div className="empty-state">За период рег и депов нет.</div>}
        </div>}
      </>}
    </section>
  );
}
