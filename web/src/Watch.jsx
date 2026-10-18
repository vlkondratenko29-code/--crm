import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import DialogReview from "./DialogReview.jsx";

// Operator watch: suspicious outgoing messages in Chatterfy (wallets, foreign links, @nicks, phones).
const when = v => v ? new Date(v.endsWith("Z") ? v : v + "Z").toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";

// Full conversations of one operator from Chatterfy, as a text file to review how they talk and close.
function DialogExport({ username }) {
  const [ops, setOps] = useState(null);
  const [op, setOp] = useState("");
  const [days, setDays] = useState("14");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { api("/api/v1/dialogs/operators", { username }).then(d => setOps(d.operators)).catch(e => setMsg(e.message)); }, [username]);
  async function run() {
    setBusy(true); setMsg("Собираю диалоги из Chatterfy… до пары минут."); setRes(null);
    try {
      const r = await api(`/api/v1/dialogs/export?operator_id=${encodeURIComponent(op)}&days=${days}&limit=30`, { username, timeout: 240000 });
      setRes(r); setMsg(r.chats ? `Готово: ${r.chats} чатов.` : "Чатов этого оператора за период не нашлось.");
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  }
  function download() {
    const blob = new Blob([res.text], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `dialogs-${res.operator.replace(/[^\wа-яё-]+/gi, "_")}-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click(); URL.revokeObjectURL(a.href);
  }
  async function copy() {
    try { await navigator.clipboard.writeText(res.text); setMsg("Скопировано."); } catch { setMsg("Скопировать не получилось — скачай файлом."); }
  }
  return (
    <div className="watch-settings">
      <div className="section-title">Диалоги оператора</div>
      <p className="recon-hint">Вся переписка чатов, где оператор писал сам или назначен в Chatterfy: клиент, бот/AI и оператор по времени. Для разбора общения и договорённостей.</p>
      <div className="pay-extra">
        <label>Оператор<select value={op} onChange={e => setOp(e.target.value)}>
          <option value="">— выбери —</option>
          {(ops || []).map(o => <option key={o.id} value={o.id}>{o.name}{o.crm ? " · @" + o.crm : ""}</option>)}
        </select></label>
        <label>Период<select value={days} onChange={e => setDays(e.target.value)}>
          <option value="7">7 дней</option><option value="14">14 дней</option><option value="30">30 дней</option><option value="60">60 дней</option>
        </select></label>
      </div>
      <button className="upload-btn" disabled={busy || !op} onClick={run}>{busy ? "Собираю…" : "Выгрузить диалоги"}</button>
      {msg && <div className="team-message">{msg}</div>}
      {res?.chats > 0 && <div className="pay-actions">
        <button className="upload-btn" onClick={download}>Скачать .txt</button>
        <button className="mini-btn" onClick={copy}>Скопировать</button>
      </div>}
    </div>
  );
}

export default function Watch({ username }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState("");
  const [allowed, setAllowed] = useState("");
  const [codes, setCodes] = useState("");
  const [showSettings, setShowSettings] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api("/api/v1/watch", { username });
      setData(d); setAllowed(d.allowed || ""); setCodes(d.codes || ""); setError("");
    } catch (e) { setError(e.message); }
  }, [username]);
  useEffect(() => { load(); }, [load]);

  async function save() {
    setBusy(true); setMsg("");
    try {
      await api("/api/v1/settings/watch", { username, method: "POST", body: { api_key: key || null, allowed, codes } });
      setKey(""); setMsg("Сохранено."); await load();
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  }
  async function runNow() {
    setBusy(true); setMsg("");
    try {
      const r = await api("/api/v1/watch/run", { username, method: "POST", timeout: 90000 });
      setMsg(r.status === "no_key" ? "Нет ключа Chatterfy — добавь его в настройках ниже." : r.error ? "Ошибка: " + r.error : `Проверено чатов: ${r.chats ?? 0}, новых нарушений: ${r.found ?? 0}, размечено из канала: ${r.tagged ?? 0}, подтянуто почт клиентов: ${r.contacts ?? 0}`);
      await load();
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  }

  const st = data?.status || {};
  return (
    <section className="role-dashboard stats-screen">
      <div className="role-hero"><div><p className="eyebrow">КОНТРОЛЬ</p>
        <h2>Сообщения операторов</h2>
        <p>Каждые 3 минуты CRM читает новые сообщения в Chatterfy и ищет кошельки, чужие ссылки, @ники и телефоны. О находке сразу пишет админам в Telegram.</p></div><span>🛡</span></div>
      {error && <div className="team-message">{error}</div>}
      {data && <>
        <div className="st-kpis">
          <div><span>Статус</span><b className={data.configured && !st.error && !data.key_problem ? "t-ftd" : "t-bad"}>{!data.configured ? "Нет ключа" : data.key_problem ? "Неверный ключ" : st.error ? "Ошибка" : "Работает"}</b><small>{st.at ? "проверка " + when(st.at) : "ещё не запускался"}</small></div>
          <div><span>Нарушений</span><b className={data.alerts.length ? "t-bad" : ""}>{data.alerts.length}</b><small>последние 100</small></div>
        </div>
        {st.error && <p className="recon-hint warn">{st.error}</p>}
        {data.key_problem && <p className="recon-hint warn">Сохранённый ключ неверный: {data.key_problem}{data.key_from_env ? " Ключ задан в Render (переменная CHATTERFY_API_KEY) — исправь его там." : " Вставь новый в «Настройках» ниже."}</p>}
        <div className="buy-upload">
          <div><b>Проверить сейчас</b><small>Не ждать следующих 3 минут</small></div>
          <button className="upload-btn" disabled={busy} onClick={runNow}>{busy ? "Проверяю…" : "Проверить"}</button>
        </div>
        {msg && <div className="team-message">{msg}</div>}

        {data.by_sender.length > 0 && <>
          <div className="section-title">Кто нарушал</div>
          <div className="st-list">{data.by_sender.map(s => <div className="st-row" key={s.sender}><div className="st-row-main"><b>{s.sender}</b></div><div className="st-row-nums"><span className="t-bad">{s.count}<small>нарушений</small></span></div></div>)}</div>
        </>}

        <div className="section-title">Находки</div>
        <div className="st-feed">
          {data.alerts.map(a => <div className="st-event watch-event" key={a.message_id + a.kind}>
            <span className="st-badge t-bad">{a.kind}</span>
            <div><b>{a.sender_name || "—"} → {a.chat_name || "клиент"}</b>
              <small>{when(a.message_at)} · {a.match}</small>
              <p className="watch-text">{a.text}</p>
              <a href={a.chat_link} target="_blank" rel="noreferrer">Открыть чат в Chatterfy</a></div>
          </div>)}
          {!data.alerts.length && <div className="empty-state">{data.configured ? "Нарушений не найдено." : "Добавь ключ Chatterfy, и проверка начнётся."}</div>}
        </div>

        <button className="link-btn" onClick={() => setShowSettings(v => !v)}>{showSettings ? "Скрыть настройки" : "Настройки"}</button>
        {showSettings && <div className="watch-settings">
          <label>Ключ сервисного аккаунта Chatterfy {data.configured && <small>(уже задан{data.key_from_env ? " в Render" : ""}; оставь пустым, чтобы не менять)</small>}
            <input type="password" value={key} onChange={e => setKey(e.target.value)} placeholder="вставь ключ" autoComplete="off" /></label>
          <label>Наши ссылки и ники (не считаются нарушением), по одному в строке
            <textarea rows={5} value={allowed} onChange={e => setAllowed(e.target.value)} /></label>
          <label>Кодовые слова канала (через запятую)
            <input value={codes} onChange={e => setCodes(e.target.value)} /></label>
          <p className="recon-hint">Клиенты, которые пришли не с рекламы, попадают в статистику как кампания «Канал · слово» — по первому сообщению. Без слова — «Канал · без кода».</p>
          <button className="upload-btn" disabled={busy} onClick={save}>Сохранить</button>
        </div>}
      </>}
      {!data && !error && <p className="subtitle">Загружаю…</p>}
      {data?.configured && <DialogExport username={username} />}
      {data?.configured && <DialogReview username={username} admin />}
    </section>
  );
}
