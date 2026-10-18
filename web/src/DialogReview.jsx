import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

// Dialog review: likely mistakes in a handler's Chatterfy chats, with a better reply for each.
// The admin starts a review for an operator; the handler reads their own reviews to learn.
const when = v => v ? new Date(v.endsWith("Z") ? v : v + "Z").toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
const SEV = { high: ["Важно", "t-bad"], medium: ["Средне", "t-money"], low: ["Мелочь", "t-reg"] };

function Report({ review }) {
  const r = review.report;
  const [open, setOpen] = useState(null);
  if (!r) return null;
  return (
    <div className="review-report">
      <div className="st-kpis">
        <div><span>Чатов</span><b>{r.chats.length}</b><small>за {review.days} дн.</small></div>
        <div><span>Замечаний</span><b className={r.findings ? "t-bad" : "t-ftd"}>{r.findings}</b><small>{r.ai ? "правила + AI" : "только правила"}</small></div>
        {r.avg_score != null && <div><span>Оценка AI</span><b className={r.avg_score >= 7 ? "t-ftd" : r.avg_score >= 5 ? "t-money" : "t-bad"}>{r.avg_score}</b><small>из 10, средняя</small></div>}
      </div>
      {r.top.length > 0 && <>
        <div className="section-title">Что повторяется</div>
        <div className="review-top">{r.top.map(t => <span key={t.title} className="st-badge">{t.title} · {t.count}</span>)}</div>
      </>}
      <div className="section-title">По чатам</div>
      <div className="st-feed">
        {r.chats.map(c => {
          const isOpen = open === c.id;
          const high = c.findings.filter(f => f.severity === "high").length;
          return <div className="st-row review-chat" key={c.id}>
            <button className="review-head" onClick={() => setOpen(isOpen ? null : c.id)}>
              <b>{c.name}</b>
              <small>{c.score != null ? `${c.score}/10 · ` : ""}{c.findings.length ? `${c.findings.length} замеч.${high ? ` (${high} важн.)` : ""}` : "без замечаний"} {isOpen ? "▴" : "▾"}</small>
            </button>
            {isOpen && <div className="review-body">
              {c.summary && <p className="watch-text">{c.summary}</p>}
              {c.good.length > 0 && <p className="review-good">👍 {c.good.join(" · ")}</p>}
              {c.ai_error && <p className="recon-hint warn">AI-разбор не получился: {c.ai_error}</p>}
              {c.findings.map((f, i) => <div className="review-finding" key={i}>
                <div><span className={"st-badge " + SEV[f.severity][1]}>{SEV[f.severity][0]}</span> <b>{f.title}</b>{f.at && <small> · {when(f.at)}</small>}</div>
                {f.quote && <blockquote>{f.quote}</blockquote>}
                <p><b>Что не так:</b> {f.problem}</p>
                {f.better && <p className="review-better"><b>Как лучше:</b> {f.better}</p>}
              </div>)}
              <a href={c.link} target="_blank" rel="noreferrer">Открыть чат в Chatterfy</a>
            </div>}
          </div>;
        })}
        {!r.chats.length && <div className="empty-state">Чатов за период не нашлось.</div>}
      </div>
    </div>
  );
}

export default function DialogReview({ username, admin }) {
  const [list, setList] = useState(null);
  const [ai, setAi] = useState(true);
  const [ops, setOps] = useState([]);
  const [op, setOp] = useState("");
  const [days, setDays] = useState("7");
  const [current, setCurrent] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const poll = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await api("/api/v1/dialogs/reviews", { username });
      setList(d.reviews); setAi(d.ai);
      return d.reviews;
    } catch (e) { setMsg(e.message); return []; }
  }, [username]);
  useEffect(() => {
    load();
    if (admin) api("/api/v1/dialogs/operators", { username }).then(d => setOps(d.operators)).catch(e => setMsg(e.message));
    return () => clearTimeout(poll.current);
  }, [load, admin, username]);

  async function openReview(id) {
    clearTimeout(poll.current);
    try {
      const r = await api(`/api/v1/dialogs/review/${id}`, { username });
      setCurrent(r);
      if (r.status === "running") poll.current = setTimeout(() => openReview(id), 5000);
      else { setBusy(false); load(); }
    } catch (e) { setMsg(e.message); setBusy(false); }
  }
  async function start() {
    setBusy(true); setMsg("Собираю чаты и разбираю их… обычно 2–5 минут, можно не ждать на этой странице."); setCurrent(null);
    try {
      const r = await api("/api/v1/dialogs/review", { username, method: "POST", body: { operator_id: op, days: Number(days) }, timeout: 60000 });
      await load(); openReview(r.id);
    } catch (e) { setMsg(e.message); setBusy(false); }
  }

  return (
    <div className="watch-settings">
      <div className="section-title">{admin ? "Разбор ошибок в диалогах" : "Разбор моих диалогов"}</div>
      <p className="recon-hint">
        {admin ? "CRM читает чаты обработчика и отмечает возможные ошибки: долгие ответы, клиент без ответа, обещания дохода, чужие контакты, нет ссылки на регистрацию"
          : "Возможные ошибки в твоих чатах: что не так и как можно было ответить лучше"}
        {ai ? ". AI (Claude) дополнительно читает переписку как тимлид и предлагает, как ответить лучше." : "."}
      </p>
      {admin && !ai && <p className="recon-hint warn">AI-разбор выключен: добавь переменную ANTHROPIC_API_KEY в Render. Пока работают только правила.</p>}
      {admin && <>
        <div className="pay-extra">
          <label>Обработчик<select value={op} onChange={e => setOp(e.target.value)}>
            <option value="">— выбери —</option>
            {ops.map(o => <option key={o.id} value={o.id}>{o.name}{o.crm ? " · @" + o.crm : ""}</option>)}
          </select></label>
          <label>Период<select value={days} onChange={e => setDays(e.target.value)}>
            <option value="3">3 дня</option><option value="7">7 дней</option><option value="14">14 дней</option><option value="30">30 дней</option>
          </select></label>
        </div>
        <button className="upload-btn" disabled={busy || !op} onClick={start}>{busy ? "Разбираю…" : "Сделать разбор"}</button>
      </>}
      {msg && <div className="team-message">{msg}</div>}

      {current?.status === "running" && <p className="subtitle">Разбор {current.operator_name} идёт…</p>}
      {current?.status === "error" && <p className="recon-hint warn">Разбор не получился: {current.error}</p>}
      {current?.status === "done" && <>
        <div className="section-title">{current.operator_name} · {when(current.created_at)}</div>
        <Report review={current} />
      </>}

      {list?.length > 0 && <>
        <div className="section-title">{admin ? "Прошлые разборы" : "Мои разборы"}</div>
        <div className="st-list">{list.map(r => <button className="st-row review-item" key={r.id} onClick={() => openReview(r.id)}>
          <div className="st-row-main"><b>{admin ? r.operator_name : `За ${r.days} дн.`}</b><small>{when(r.created_at)}</small></div>
          <small>{r.status === "running" ? "идёт…" : r.status === "error" ? "ошибка" : `${r.chats} чатов · ${r.findings} замечаний${r.avg_score != null ? ` · оценка ${r.avg_score}/10` : ""}`}</small>
        </button>)}</div>
      </>}
      {list && !list.length && !admin && <div className="empty-state">Разборов пока нет — их делает админ.</div>}
    </div>
  );
}
