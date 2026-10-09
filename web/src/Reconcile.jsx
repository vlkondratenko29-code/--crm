import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

const FIELD_LABELS = {
  account_id: "Счёт", email: "Email", phone: "Телефон", label: "Метка", name: "Имя", country: "Страна",
  registration_date: "Дата рег", ftd_date: "Дата FTD", ftd_amount: "Сумма FTD", deposits: "Депозиты",
  withdrawals: "Выводы", balance: "Баланс", last_trade_date: "Последняя сделка",
};

const money = (v) => (v == null || v === "" ? "—" : "$" + Number(v).toFixed(0));

function Section({ title, hint, rows, empty, render }) {
  const [open, setOpen] = useState(false);
  const shown = open ? rows : rows.slice(0, 5);
  return (
    <>
      <div className="section-title">{title} <span className="count-pill">{rows.length}</span></div>
      {hint && <p className="recon-hint">{hint}</p>}
      <div className="recent-list">
        {shown.map(render)}
        {!rows.length && <div className="empty-state">{empty}</div>}
      </div>
      {rows.length > 5 && <button className="load-more" onClick={() => setOpen(o => !o)}>{open ? "Свернуть" : `Показать все (${rows.length})`}</button>}
    </>
  );
}

export default function Reconcile({ username, isAdmin }) {
  const [broker, setBroker] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try { setData(await api("/api/v1/reconciliation?broker=" + encodeURIComponent(broker), { username })); }
    catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [broker, username]);
  useEffect(() => { load(); }, [load]);

  const c = data?.counts || {};
  const leadRow = (extra) => (x, i) => (
    <div key={x.lead_key + i}>
      <div><b>{x.email || x.name || x.lead_key}</b><small>{[x.campaign, x.source].filter(Boolean).join(" · ") || "Без атрибуции"}</small></div>
      <span>{extra(x)}</span>
    </div>
  );

  return (
    <section className="role-dashboard recon-dashboard">
      <div className="role-hero"><div><p className="eyebrow">СВЕРКА</p><h2>Chatterfy и брокеры</h2><p>Кто задепал по данным Chatterfy и что показывают отчёты брокеров.</p></div><span>⚖️</span></div>

      <div className="buying-filters recon-filters">
        <select value={broker} onChange={e => setBroker(e.target.value)}>
          <option value="">Все брокеры</option>
          {(data?.brokers || []).map(b => <option key={b} value={b}>{b}</option>)}
        </select>
        <button className="load-more" onClick={load} disabled={loading}>{loading ? "Проверяю…" : "Обновить"}</button>
      </div>
      {error && <div className="team-message">{error}</div>}

      {data && <>
        <div className="exec-kpis">
          <div><span>НАЙДЕНО ЛИДОВ</span><b>{c.matched} / {c.leads}</b></div>
          <div><span>ПО UID</span><b>{c.by_uid ?? 0}</b></div>
          <div><span>ПО МЕТКЕ</span><b>{c.by_label}</b></div>
          <div><span>ПО EMAIL</span><b>{c.by_email}</b></div>
          <div><span>ПО ТЕЛЕФОНУ</span><b>{c.by_phone}</b></div>
          <div><span>КЛИЕНТОВ У БРОКЕРА</span><b>{c.broker_clients}</b></div>
          <div><span>МЕТКА ЗАПОЛНЕНА</span><b>{c.label_coverage}%</b></div>
        </div>
        {c.label_coverage < 50 && <p className="recon-hint warn">У большинства строк брокера пустая метка (Label). Добавь Click ID из Chatterfy в партнёрскую ссылку, чтобы он приходил в отчёт брокера. Пока сверка идёт по UID, email и телефону.</p>}

        <Section title="FTD в Chatterfy, у брокера депозита нет" rows={data.ftd_no_deposit}
          hint="Chatterfy записал депозит, но в отчёте брокера нет такого клиента или нет депозита."
          empty="Проверять нечего."
          render={leadRow(x => (x.reason === "not_found" ? "не найден" : "нет депозита") + (x.chatterfy_ftd != null ? " · " + money(x.chatterfy_ftd) : ""))} />

        <Section title="Депозит у брокера, нет FTD в Chatterfy" rows={data.deposit_no_ftd}
          hint="У брокера есть деньги от этого лида, но Chatterfy не получил FTD. Возможно, не дошёл постбэк."
          empty="Проверять нечего."
          render={leadRow(x => `${x.stage} · брокер ${money(x.broker_deposits)}`)} />

        <Section title="Сумма FTD не совпадает" rows={data.amount_mismatch}
          empty="Все суммы FTD совпадают."
          render={leadRow(x => `Chatterfy ${money(x.chatterfy_ftd)} · брокер ${money(x.broker_ftd ?? x.broker_deposits)}`)} />

        <Section title="Регистрация в Chatterfy, у брокера не найден" rows={data.reg_not_found}
          empty="Все зарегистрированные лиды найдены."
          render={leadRow(x => "REG")} />

        <Section title="Клиенты брокера без лида в Chatterfy" rows={data.unattributed}
          hint="Клиенты из отчётов брокера, которых не удалось связать ни с одним лидом. Сортировка по депозитам."
          empty="Все клиенты брокера связаны."
          render={(x, i) => (
            <div key={x.broker + x.account_id + i}>
              <div><b>{x.email || x.name || x.account_id}</b><small>{x.broker} · #{x.account_id}{x.country ? " · " + x.country : ""}{x.accounts > 1 ? ` · счетов: ${x.accounts}` : ""}</small></div>
              <span>{money(x.deposits)}</span>
            </div>
          )} />
      </>}

      {isAdmin && <ChatterfyPush username={username} />}
      {isAdmin && <ChatterfyUpload username={username} onDone={load} />}
      {isAdmin && <BrokerUpload username={username} brokers={data?.brokers || []} onDone={load} />}
    </section>
  );
}

export function pushSummary(p) {
  if (!p) return "";
  if (p.error) return "Отправка в Chatterfy: ошибка — " + p.error;
  if (!p.configured) return "Отправка в Chatterfy не настроена (вкладка «Сверка» → «Регистрации и депозиты → Chatterfy»).";
  if (p.dry_run) return `Готово к отправке: ${p.candidates - p.no_click_id} событий` + (p.no_click_id ? ` · без Click ID (не отправить): ${p.no_click_id}` : "");
  return `В Chatterfy отправлено: ${p.sent}` + (p.failed ? ` · ошибок: ${p.failed}` : "") + (p.no_click_id ? ` · без Click ID: ${p.no_click_id}` : "");
}

function ChatterfyPush({ username }) {
  const [url, setUrl] = useState("");
  const [saved, setSaved] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api("/api/v1/settings/chatterfy-postback", { username }).then(r => { setUrl(r.url || ""); setSaved(r); }).catch(e => setMsg(e.message));
  }, [username]);

  async function save() {
    setBusy(true); setMsg("");
    try { await api("/api/v1/settings/chatterfy-postback", { username, method: "POST", body: { url } }); setMsg("Ссылка сохранена."); }
    catch (e) { setMsg(e.message); }
    finally { setBusy(false); }
  }
  async function push(dry) {
    setBusy(true); setMsg("");
    try { setMsg(pushSummary(await api("/api/v1/chatterfy/push?dry_run=" + (dry ? "true" : "false"), { username, method: "POST", timeout: 120000 }))); }
    catch (e) { setMsg(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="recon-upload">
      <div className="section-title">Регистрации и депозиты → Chatterfy</div>
      <p className="recon-hint">После загрузки отчёта брокера CRM сама сообщает в Chatterfy, кто зарегистрировался и кто внёс депозит (с суммой). Там клиенту ставятся этап и тег. Вставь сюда ссылку Custom Postback из Chatterfy (Tracker → Integrations). Лиды без Click ID отправить нельзя.</p>
      <div className="note-form">
        <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://… ссылка постбэка из Chatterfy" />
        <button className="primary" disabled={busy} onClick={save}>Сохранить</button>
      </div>
      <div className="note-form" style={{ marginTop: 8 }}>
        <button className="load-more" disabled={busy || !url} onClick={() => push(true)}>Проверить, что уйдёт</button>
        <button className="load-more" disabled={busy || !url} onClick={() => push(false)}>Отправить сейчас</button>
      </div>
      {saved?.pushes && Object.keys(saved.pushes).length > 0 && <p className="recon-hint">Уже отправлено: {saved.pushes.sent || 0}{saved.pushes.failed ? ` · с ошибкой: ${saved.pushes.failed}` : ""}</p>}
      {msg && <div className="team-message">{msg}</div>}
    </div>
  );
}

function ChatterfyUpload({ username, onDone }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  async function upload(e) {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    setBusy(true); setMsg("");
    try {
      const form = new FormData();
      files.forEach(f => form.append("files", f));
      const r = await api("/api/v1/chatterfy/import", { username, method: "POST", body: form, timeout: 120000 });
      setMsg(`Новых лидов: ${r.created} · обновлено: ${r.updated} · REG/FTD из тегов: ${r.events_from_tags}`);
      onDone?.();
    } catch (err) { setMsg(err.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="recon-upload">
      <div className="section-title">Загрузить пользователей Chatterfy</div>
      <p className="recon-hint">Chatterfy → бот → Users → Export CSV. Добавляет в CRM все чаты (и тех, кто писал только в личку) вместе с тегами. Один файл на каждого бота.</p>
      <label className={"upload-btn" + (busy ? " busy" : "")}>
        {busy ? "Загружаю…" : "Выбрать chats.csv"}
        <input type="file" accept=".csv,text/csv" multiple disabled={busy} onChange={upload} />
      </label>
      {msg && <div className="team-message">{msg}</div>}
    </div>
  );
}

function BrokerUpload({ username, brokers, onDone }) {
  const [broker, setBroker] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  async function upload(e) {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    if (!broker.trim()) { setError("Сначала впиши название брокера, например FxPro"); return; }
    setBusy(true); setError(""); setResult(null);
    try {
      const form = new FormData();
      form.append("broker", broker.trim());
      files.forEach(f => form.append("files", f));
      const res = await api("/api/v1/broker/import", { username, method: "POST", body: form, timeout: 120000 });
      setResult(res);
      onDone?.();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="recon-upload">
      <div className="section-title">Загрузить отчёт брокера</div>
      <p className="recon-hint">CSV из партнёрского кабинета любого брокера. Колонки распознаются сами (email, телефон, метка / sub ID, депозиты, даты). Для FxPro бери отчёт «Clients».</p>
      <div className="note-form">
        <input list="broker-names" value={broker} onChange={e => setBroker(e.target.value)} placeholder="Брокер, например FxPro" />
        <datalist id="broker-names">{brokers.map(b => <option key={b} value={b} />)}</datalist>
        <label className={"upload-btn" + (busy ? " busy" : "")}>
          {busy ? "Загружаю…" : "Выбрать CSV"}
          <input type="file" accept=".csv,text/csv" multiple disabled={busy} onChange={upload} />
        </label>
      </div>
      {error && <div className="team-message">{error}</div>}
      {result && <div className="team-message">
        <b>{result.broker}: загружено строк — {result.accounts}.</b> С меткой {result.with_label}, с email {result.with_email}, с телефоном {result.with_phone}, с депозитом {result.with_deposit}.
        {result.missing_keys?.length > 0 && <> Не нашёл колонки: {result.missing_keys.join(", ")}.</>}
        {result.chatterfy_push && <div>{pushSummary(result.chatterfy_push)}</div>}
        {(result.columns || []).map(f => <div key={f.file} className="col-map">{f.file}: {Object.entries(f.columns).map(([k, v]) => `${FIELD_LABELS[k] || k} ← "${v}"`).join(" · ") || "известных колонок нет"}</div>)}
      </div>}
    </div>
  );
}
