import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import { LeadCard, LeadRow } from "./Leads.jsx";

// Handler home screen: what to do today with my leads, plus free leads to take.
const GROUPS = [
  { key: "callback", title: "Перезвонить", hint: "Ты отметил «Перезвонить» — клиент ждёт ответа." },
  { key: "reg_no_ftd", title: "Зарегался, но нет депозита", hint: "Самые тёплые: дожми до первого депозита." },
  { key: "no_answer", title: "Не отвечают", hint: "Напиши ещё раз или попробуй другой подход." },
  { key: "stale", title: "Без движения больше 2 дней", hint: "Лид завис — обнови статус или напиши клиенту." },
  { key: "active", title: "В работе", hint: "" },
];

export default function MyDay({ username, onOpenLeads }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);

  const load = useCallback(async () => {
    try { setData(await api("/api/v1/leads/today", { username })); setError(""); }
    catch (e) { setError(e.message); }
  }, [username]);
  useEffect(() => { load(); }, [load]);

  if (selected) {
    return <LeadCard leadKey={selected} username={username} canAssign={false} team={[]}
      onBack={() => { setSelected(null); load(); }} backLabel="← Мой день" />;
  }

  const s = data?.stats || {};
  const todo = GROUPS.filter(g => g.key !== "active").reduce((a, g) => a + (data?.[g.key]?.count || 0), 0);

  return (
    <section className="role-dashboard handler-dashboard">
      <div className="role-hero"><div><p className="eyebrow">ОБРАБОТЧИК</p><h2>Мой день</h2><p>Кому написать сейчас и какие лиды можно взять.</p></div><span>🎧</span></div>
      {error && <div className="team-message">{error}</div>}
      <div className="mini-metrics">
        <div><span>Сделать сегодня</span><b>{data ? todo : "…"}</b></div>
        <div><span>Мои лиды</span><b>{data ? s.mine : "…"}</b></div>
        <div><span>Мои FTD</span><b>{data ? s.mine_ftd : "…"}</b></div>
      </div>

      {data && GROUPS.map(g => {
        const block = data[g.key];
        if (!block?.count) return null;
        return <React.Fragment key={g.key}>
          <div className="section-title">{g.title} <span className="count-pill">{block.count}</span></div>
          {g.hint && <p className="recon-hint">{g.hint}</p>}
          <div className="lead-list">{block.rows.map(lead => <LeadRow key={lead.lead_key} lead={lead} onClick={() => setSelected(lead.lead_key)} />)}</div>
          {block.count > block.rows.length && <button className="load-more" onClick={onOpenLeads}>Ещё {block.count - block.rows.length} во вкладке «Лиды»</button>}
        </React.Fragment>;
      })}
      {data && !s.mine && <div className="empty-state">У тебя пока нет своих лидов. Возьми свободный лид ниже — он закрепится за тобой.</div>}
      {data && s.mine > 0 && !todo && !data.active?.count && <div className="empty-state">Все свои лиды обработаны 👍</div>}

      <div className="section-title">Свободные лиды <span className="count-pill">{data?.free?.count ?? "…"}</span></div>
      <p className="recon-hint">Новые лиды без ответственного. Открой и нажми «Взять лид». Новых за сегодня: {s.new_today ?? "…"}.</p>
      <div className="lead-list">
        {(data?.free?.rows || []).map(lead => <LeadRow key={lead.lead_key} lead={lead} onClick={() => setSelected(lead.lead_key)} />)}
        {data && !data.free?.count && <div className="empty-state">Свободных лидов нет.</div>}
      </div>
      {data && data.free?.count > (data.free?.rows || []).length && <button className="load-more" onClick={onOpenLeads}>Все свободные лиды →</button>}
    </section>
  );
}
