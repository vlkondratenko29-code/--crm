import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { authHeaders } from "./api.js";
import Leads, { LeadCard } from "./Leads.jsx";
import MyDay from "./MyDay.jsx";
import Reconcile from "./Reconcile.jsx";

const tg = window.Telegram?.WebApp;
// Outside Telegram (plain browser) there is no signed user. The demo admin is
// only for `npm run dev`; production builds start as a no-access guest.
const demoUser = import.meta.env.DEV ? { username: "jokwq", role: "admin", is_admin: true } : { username: "", role: "guest", is_admin: false };


function App() {
  const telegramUser = tg?.initDataUnsafe?.user;
  const user = useMemo(() => {
    if (!telegramUser) return demoUser;
    const username = telegramUser.username || "";
    const admin = ["jokwq", "nodari777"].includes(username.toLowerCase());
    return { username, role: admin ? "admin" : "handler", is_admin: admin };
  }, [telegramUser]);
  const [query, setQuery] = useState("");
  const [roleInfo, setRoleInfo] = useState(null);
  const [accessError, setAccessError] = useState("");
  const [client, setClient] = useState(null);
  const [results, setResults] = useState([]);
  const [searched, setSearched] = useState(false);
  const [period, setPeriod] = useState("0");
  const [debugInfo, setDebugInfo] = useState(null);
  const [debugLoading, setDebugLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importMessage, setImportMessage] = useState("");
  const [stats, setStats] = useState({ leads: 0, reg: 0, ftd: 0, ft: 0, deposits: 0 });
  const [finance, setFinance] = useState(null);
  const [traffic, setTraffic] = useState([]);
  const [operations, setOperations] = useState(null);
  const [alerts, setAlerts] = useState(null);
  const [trafficCampaign, setTrafficCampaign] = useState("all");
  const [trafficSource, setTrafficSource] = useState("all");
  const [tab, setTab] = useState("dashboard");
  const [team, setTeam] = useState([]);
  const [newUser, setNewUser] = useState("");
  const [newRole, setNewRole] = useState("handler");
  const [teamMessage, setTeamMessage] = useState("");
  const [savingUser, setSavingUser] = useState(false);
  const [previewRole, setPreviewRole] = useState("");
  const [buyingCampaign, setBuyingCampaign] = useState("all");
  const [buyingSource, setBuyingSource] = useState("all");
  const actualUser = roleInfo ? { ...user, ...roleInfo, is_admin: roleInfo.role === "admin" } : user;
  const effectiveUser = actualUser.is_admin && previewRole ? { ...actualUser, role: previewRole, is_admin: true } : actualUser;
  // `main` tabs sit in the bottom bar; the rest open from «Ещё».
  const roleMeta = {
    admin: { label: "АДМИН", title: "Полный доступ к CRM", main: ["dashboard", "leads", "clients", "reconcile"], more: ["stats", "finance", "traffic", "operations", "alerts", "admin"] },
    head_buying: { label: "ХЭД БАИНГА", title: "Баинг и результаты", main: ["dashboard", "leads", "clients", "traffic"], more: ["reconcile", "stats", "finance"] },
    seo: { label: "SEO", title: "Трафик и воронка", main: ["dashboard", "clients", "stats"], more: [] },
    handler: { label: "ОБРАБОТЧИК", title: "Работа с клиентами", main: ["dashboard", "leads", "clients"], more: [] },
  }[effectiveUser.role] || { label: "ОБРАБОТЧИК", title: "Работа с клиентами", main: ["dashboard", "leads", "clients"], more: [] };
  roleMeta.tabs = [...roleMeta.main, ...roleMeta.more];
  const canSee = (id) => roleMeta.tabs.includes(id);
  const leadRoles = ["admin", "head_buying", "handler"];
  const [moreOpen, setMoreOpen] = useState(false);
  const [openLead, setOpenLead] = useState(null);
  // If a role switch (preview) hides the current tab, fall back to the home screen.
  React.useEffect(() => { if (!canSee(tab)) setTab("dashboard"); }, [effectiveUser.role]);
  const buyingRows = useMemo(() => (stats.chatterfy?.attribution || []).filter(x => (buyingCampaign === "all" || x.campaign === buyingCampaign) && (buyingSource === "all" || x.source === buyingSource)), [stats.chatterfy?.attribution, buyingCampaign, buyingSource]);
  const buyingTotals = useMemo(() => buyingRows.reduce((a, x) => ({ leads: a.leads + Number(x.leads || 0), reg: a.reg + Number(x.reg || 0), ftd: a.ftd + Number(x.ftd || 0), ft: a.ft + Number(x.ft || 0), deposits: a.deposits + Number(x.deposits || 0) }), { leads: 0, reg: 0, ftd: 0, ft: 0, deposits: 0 }), [buyingRows]);

  React.useEffect(() => {
    tg?.ready?.();
    tg?.expand?.();
    const loadMe = async () => {
      try {
        const base = import.meta.env.VITE_API_URL || "";
        const response = await fetch(base + "/api/v1/me", {
          headers: authHeaders(effectiveUser.username)
        });
        if (response.ok) { setRoleInfo(await response.json()); setAccessError(""); }
        else {
          const data = await response.json().catch(() => ({}));
          setAccessError(data.detail || "CRM access is not granted");
        }
      } catch (error) {
        console.error(error);
      }
    };
    const loadDashboard = async () => {
      try {
        const base = import.meta.env.VITE_API_URL || "";
        const response = await fetch(`${base}/api/v1/dashboard?days=${period}`, {
          headers: authHeaders(effectiveUser.username)
        });
        if (!response.ok) return;
        const data = await response.json();
        setStats(data);
      } catch (error) {
        console.error(error);
      }
    };
    loadMe();
    loadDashboard();
  }, [user.username, period]);

  React.useEffect(() => {
    if (effectiveUser.is_admin) loadTeam();
  }, [effectiveUser.username, effectiveUser.is_admin]);

  React.useEffect(() => { if (canSee("finance")) loadFinance(); if (canSee("traffic")) loadTraffic(); if (canSee("operations")) loadOperations(); if (canSee("alerts")) loadAlerts(); }, [effectiveUser.username, effectiveUser.role, tab]);

  async function loadTraffic() { if (!canSee("traffic")) return; try { const base = import.meta.env.VITE_API_URL || ""; const res = await fetch(base + "/api/v1/traffic", { headers: authHeaders(effectiveUser.username) }); if (res.ok) setTraffic((await res.json()).rows || []); } catch (error) { console.error(error); } }

  async function loadOperations() { if (!canSee("operations")) return; try { const base = import.meta.env.VITE_API_URL || ""; const res = await fetch(base + "/api/v1/operations", { headers: authHeaders(effectiveUser.username) }); if (res.ok) setOperations(await res.json()); } catch (error) { console.error(error); } }
  async function loadAlerts() { if (!canSee("alerts")) return; try { const base = import.meta.env.VITE_API_URL || ""; const res = await fetch(base + "/api/v1/alerts", { headers: authHeaders(effectiveUser.username) }); if (res.ok) setAlerts(await res.json()); } catch (error) { console.error(error); } }
  async function loadFinance() { if (!["admin", "head_buying"].includes(effectiveUser.role)) return; try { const base = import.meta.env.VITE_API_URL || ""; const res = await fetch(base + "/api/v1/finance", { headers: authHeaders(effectiveUser.username) }); if (res.ok) setFinance(await res.json()); } catch (error) { console.error(error); } }

  async function loadTeam() {
    if (!effectiveUser.is_admin) return;
    const base = import.meta.env.VITE_API_URL || "";
    const res = await fetch(base + "/api/v1/users", { headers: authHeaders(effectiveUser.username) });
    if (res.ok) setTeam((await res.json()).users || []);
  }

  async function saveTeamUser(event) {
    event?.preventDefault();
    const username = newUser.trim().replace(/^@+/, "").toLowerCase();
    if (!username) {
      setTeamMessage("Введи Telegram username, например @handler1");
      return;
    }
    setSavingUser(true);
    setTeamMessage("");
    try {
      const base = import.meta.env.VITE_API_URL || "";
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30000);
      const res = await fetch(base + "/api/v1/users", {
        method: "POST",
        signal: controller.signal,
        headers: { "Content-Type": "application/json", ...authHeaders(effectiveUser.username) },
        body: JSON.stringify({ username, role: newRole, active: true })
      });
      clearTimeout(timer);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || ("Ошибка сервера: HTTP " + res.status));
      setNewUser("");
      setTeamMessage("@" + username + " добавлен как " + (ROLE_LABELS[newRole] || newRole));
      await loadTeam();
    } catch (error) {
      setTeamMessage(error.name === "AbortError" ? "CRM не ответила за 30 секунд. Проверь, что Render и Supabase доступны." : error.message);
    } finally {
      setSavingUser(false);
    }
  }

  async function disableTeamUser(username) {
    const base = import.meta.env.VITE_API_URL || "";
    const res = await fetch(base + "/api/v1/users/" + encodeURIComponent(username), {
      method: "DELETE",
      headers: authHeaders(effectiveUser.username)
    });
    if (res.ok) await loadTeam();
  }

  async function search() {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const base = import.meta.env.VITE_API_URL || "";
      const response = await fetch(`${base}/api/v1/clients/search?q=${encodeURIComponent(query)}`, {
        headers: authHeaders(effectiveUser.username)
      });
      if (!response.ok) throw new Error("Search failed");
      const data = await response.json();
      const found = data.clients || [];
      setResults(found);
      setClient(found.length === 1 ? found[0] : null);
      setSearched(true);
      setDebugInfo(null);
    } catch (error) {
      console.error(error);
      setClient(null);
      setResults([]);
      setSearched(true);
    } finally {
      setLoading(false);
    }
  }

  async function debugClientLink() {
    if (!client?.email || !effectiveUser.is_admin) return;
    setDebugLoading(true);
    try {
      const base = import.meta.env.VITE_API_URL || "";
      const res = await fetch(base + "/api/v1/clients/debug?q=" + encodeURIComponent(client.email), {
        headers: authHeaders(effectiveUser.username)
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Debug failed");
      setDebugInfo(data);
    } catch (error) {
      setDebugInfo({ error: error.message });
    } finally { setDebugLoading(false); }
  }

  function go(id) { setTab(id); setMoreOpen(false); setOpenLead(null); window.scrollTo?.(0, 0); }

  if (accessError && !roleInfo) {
    return (
      <main className="app">
        <header><div><p className="eyebrow">BROKER CRM</p><h1>Нет доступа</h1></div></header>
        <section className="card">
          <h2>{accessError}</h2>
          <p className="subtitle">{telegramUser?.username ? `Ты вошёл как @${telegramUser.username}. Попроси админа добавить тебя во вкладке «Команда».` : "Открой CRM через Telegram-бота. У твоего аккаунта Telegram должен быть username."}</p>
        </section>
      </main>
    );
  }

  return (
    <main className={"app role-" + effectiveUser.role}>
      <header>
        <div><p className="eyebrow">BROKER CRM</p><h1>{tab === "dashboard" && effectiveUser.role === "handler" ? "Мой день" : TAB_LABELS[tab]}</h1><p className="subtitle">FxPro · Chatterfy · сборка 09.10</p></div>
        <div className="avatar">{effectiveUser.username?.[0]?.toUpperCase() || "?"}</div>
      </header>

      <section className="userbar">
        <span>@{effectiveUser.username || "telegram-user"}</span>
        <strong>{roleMeta.label}</strong>
      </section>

      {((tab === "dashboard" && effectiveUser.role !== "handler") || tab === "stats") && <div className="period-switch">
        {[["0", "За всё время"], ["1", "Сегодня"], ["7", "7 дней"], ["30", "30 дней"], ["90", "90 дней"]].map(([v, label]) => <button key={v} className={period === v ? "selected" : ""} onClick={() => setPeriod(v)}>{label}</button>)}
      </div>}

      {effectiveUser.is_admin && tab === "dashboard" && <section className="admin-card">
        <p className="eyebrow">ДОСТУП АДМИНА</p>
        <h2>Полный доступ к CRM</h2>
        <p>Посмотреть CRM глазами другой роли:</p>
        <div className="role-preview">
          <span>Роль</span>
          <button className={!previewRole ? "selected" : ""} onClick={() => setPreviewRole("")}>Админ</button>
          <button className={previewRole === "head_buying" ? "selected" : ""} onClick={() => setPreviewRole("head_buying")}>Хэд баинга</button>
          <button className={previewRole === "seo" ? "selected" : ""} onClick={() => setPreviewRole("seo")}>SEO</button>
          <button className={previewRole === "handler" ? "selected" : ""} onClick={() => setPreviewRole("handler")}>Обработчик</button>
        </div>
        {previewRole && <div className="preview-note">Это только просмотр — твой доступ админа не меняется. В «Моём дне» ты видишь лиды, закреплённые за тобой.</div>}
        <label className="upload-btn">
          {importing ? "Загружаю…" : "Загрузить отчёты FxPro"}
          <input type="file" accept=".csv,text/csv" multiple disabled={importing} onChange={async (e) => {
            const files = Array.from(e.target.files || []);
            if (!files.length) return;
            setImporting(true); setImportMessage("");
            try {
              const base = import.meta.env.VITE_API_URL || "";
              const form = new FormData();
              files.forEach(file => form.append("files", file));
              const res = await fetch(`${base}/api/v1/broker/fxpro/import`, {
                method: "POST", body: form,
                headers: authHeaders(effectiveUser.username)
              });
              const responseText = await res.text();
              let data;
              try {
                data = JSON.parse(responseText);
              } catch {
                throw new Error(responseText.replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ").trim().slice(0, 300) || `Ошибка загрузки (HTTP ${res.status})`);
              }
              if (!res.ok) throw new Error(data.detail || "Ошибка загрузки");
              setImportMessage(`Загружено клиентов: ${data.client_rows || 0}, счетов FxPro: ${data.account_rows || 0} · связано по email: ${data.email_linked_clients || 0} · файлов: ${data.files}`);
              const dashboard = await fetch(`${base}/api/v1/dashboard?days=${period}`, {
                headers: authHeaders(effectiveUser.username)
              });
              if (dashboard.ok) setStats(await dashboard.json());
            } catch (err) {
              setImportMessage(err.message);
            } finally { setImporting(false); e.target.value = ""; }
          }} />
        </label>
        <button className="upload-btn admin-shortcut" onClick={() => go("admin")}>Команда и роли →</button>
        {importMessage && <p>{importMessage}</p>}
      </section>}

      {tab === "dashboard" && effectiveUser.role === "admin" && <section className="role-dashboard admin-dashboard">
        <div className="role-hero"><div><p className="eyebrow">ЦЕНТР УПРАВЛЕНИЯ</p><h2>Вся команда в одном месте</h2><p>Трафик → клиенты → депозиты → результат.</p></div><span>👑</span></div>
        <div className="exec-kpis">
          <div><span>ЛИДЫ</span><b>{stats.leads}</b></div><div><span>REG</span><b>{stats.reg}</b></div><div><span>FTD</span><b>{stats.ftd}</b></div><div><span>FT</span><b>{stats.ft}</b></div>
          <div><span>ДЕПОЗИТЫ</span><b>{"$" + Number(stats.deposits || 0).toFixed(2)}</b></div><div><span>ЧИСТЫЕ ДЕПОЗИТЫ</span><b>{"$" + Number(stats.company?.[0]?.net_deposits || 0).toFixed(2)}</b></div>
        </div>
        <div className="section-title">Конверсия воронки</div>
        <div className="mini-metrics"><div><span>REG → FTD</span><b>{stats.funnel?.reg_to_ftd ?? 0}%</b></div><div><span>FTD → FT</span><b>{stats.funnel?.ftd_to_ft ?? 0}%</b></div><div><span>REG → FT</span><b>{stats.funnel?.reg_to_ft ?? 0}%</b></div></div>
        <div className="section-title">Результат по GEO</div>
        <div className="geo-table">{(stats.geo || []).slice(0,6).map(x => <div className="geo-row" key={x.country}><b>{x.country}</b><span>{x.reg} REG</span><span>{x.ftd} FTD</span><strong>{"$" + Number(x.deposits || 0).toFixed(0)}</strong></div>)}</div>
        <div className="section-title">Операции</div>
        <div className="ops-grid">
          <div><span>Связано с Chatterfy</span><b>{stats.operations?.attributed_clients ?? 0}</b></div>
          <div><span>Без атрибуции</span><b>{stats.operations?.unattributed_clients ?? 0}</b></div>
          <div><span>Счета FxPro</span><b>{stats.operations?.fxpro_accounts ?? 0}</b></div>
        </div>
      </section>}
      {tab === "dashboard" && effectiveUser.role === "head_buying" && <section className="role-dashboard buying-dashboard">
        <div className="role-hero"><div><p className="eyebrow">БАИНГ · CHATTERFY</p><h2>Результаты баинга</h2><p>Атрибуция из Chatterfy · результат по Click ID.</p></div><span>📊</span></div>
        <div className="funnel"><div><span>ЛИДЫ</span><b>{buyingTotals.leads}</b></div><i>→</i><div><span>REG</span><b>{buyingTotals.reg}</b></div><i>→</i><div><span>FTD</span><b>{buyingTotals.ftd}</b></div><i>→</i><div><span>FT</span><b>{buyingTotals.ft}</b></div></div>
        <div className="mini-metrics"><div><span>REG → FTD</span><b>{buyingTotals.reg ? (buyingTotals.ftd / buyingTotals.reg * 100).toFixed(1) : "0.0"}%</b></div><div><span>FTD → FT</span><b>{buyingTotals.ftd ? (buyingTotals.ft / buyingTotals.ftd * 100).toFixed(1) : "0.0"}%</b></div><div><span>Средний FTD</span><b>{buyingTotals.ftd ? (buyingTotals.deposits / buyingTotals.ftd).toFixed(2) : "0.00"}</b></div></div>
        <div className="buying-filters"><select value={buyingCampaign} onChange={e => setBuyingCampaign(e.target.value)}><option value="all">Все кампании</option>{[...new Set((stats.chatterfy?.attribution || []).map(x => x.campaign).filter(Boolean))].sort().map(x => <option key={x} value={x}>{x}</option>)}</select><select value={buyingSource} onChange={e => setBuyingSource(e.target.value)}><option value="all">Все источники</option>{[...new Set((stats.chatterfy?.attribution || []).map(x => x.source).filter(Boolean))].sort().map(x => <option key={x} value={x}>{x}</option>)}</select></div>
        <div className="section-title">Chatterfy · результат по кампаниям</div>
        <div className="buying-summary">
          <div><span>Лиды с атрибуцией</span><b>{buyingTotals.leads}</b></div>
          <div><span>FTD с атрибуцией</span><b>{buyingTotals.ftd}</b></div>
          <div><span>Депозиты с атрибуцией</span><b>${buyingTotals.deposits.toFixed(2)}</b></div>
        </div>
        <div className="attr-header"><span>КАМПАНИЯ / ИСТОЧНИК</span><span>ADSET · AD · PLACEMENT</span><span>РЕЗУЛЬТАТ</span></div>
        <div className="click-table attribution-table">
          {buyingRows.map((x, i) => <div className="attribution-row" key={x.click_id + "-" + i}>
            <div className="attr-main"><b>{x.campaign}</b><small>{x.source}</small><em>{x.click_id}</em></div>
            <div className="attr-meta"><span>{x.adset}</span><span>{x.ad}</span><span>{x.placement}</span></div>
            <div className="attr-stats"><b>{x.leads}</b><span>LEAD</span><strong>{x.ftd} FTD</strong><span>${Number(x.deposits || 0).toFixed(2)}</span></div>
          </div>)}
          {!(stats.chatterfy?.attribution || []).length && <div className="empty-state">Атрибуция появится, когда придёт первый клиент FxPro, связанный с Chatterfy.</div>}
        </div>
        <div className="section-title">Chatterfy · топ Click ID</div>
        <div className="click-table">{(stats.chatterfy?.clicks || []).map(x => <div className="click-row" key={x.click_id}><span className="click-id">{x.click_id}</span><b>{x.leads}</b><span>{x.reg_to_ftd}% FTD</span><strong>{x.ftd} FTD</strong></div>)}</div>
        <div className="section-title">Топ стран</div><div className="country-list">{(stats.top_countries || []).map(x => <div key={x.country}><span>{x.country}</span><b>{x.count}</b></div>)}</div>
      </section>}
      {tab === "dashboard" && effectiveUser.role === "seo" && <section className="role-dashboard seo-dashboard">
        <div className="role-hero"><div><p className="eyebrow">SEO</p><h2>Трафик и воронка</h2><p>Объём лидов и конверсия без финансовых данных.</p></div><span>📈</span></div>
        <div className="mini-metrics"><div><span>Лиды</span><b>{stats.leads}</b></div><div><span>REG → FTD</span><b>{stats.funnel?.reg_to_ftd ?? 0}%</b></div><div><span>REG → FT</span><b>{stats.funnel?.reg_to_ft ?? 0}%</b></div></div>
        <div className="section-title">Топ стран</div><div className="country-list">{(stats.top_countries || []).map(x => <div key={x.country}><span>{x.country}</span><b>{x.count}</b></div>)}</div>
      </section>}
      {tab === "dashboard" && effectiveUser.role === "handler" && <MyDay username={effectiveUser.username} onOpenLeads={() => go("leads")} />}

      {tab === "leads" && canSee("leads") && <Leads username={effectiveUser.username} role={effectiveUser.role} />}
      {tab === "reconcile" && canSee("reconcile") && <Reconcile username={effectiveUser.username} isAdmin={actualUser.is_admin} />}

      {tab === "clients" && openLead && <LeadCard leadKey={openLead} username={effectiveUser.username}
        canAssign={["admin", "head_buying"].includes(effectiveUser.role)} team={team}
        onBack={() => setOpenLead(null)} backLabel="← К поиску" />}
      {tab === "clients" && !openLead && <section className="search-card">
        <h2>Найти клиента</h2>
        <div className="search">
          <input value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => e.key === "Enter" && search()} placeholder="Имя, @username, UID, телефон, email" />
          <button onClick={search}>Найти</button>
        </div>
        <p className="recon-hint search-hint">Ищет по имени и @username из Telegram, UID у брокера, телефону, email, Click ID и логину FxPro.</p>
      </section>}
      {tab === "stats" && canSee("stats") && <section className="role-dashboard analytics-dashboard">
        <div className="role-hero"><div><p className="eyebrow">АНАЛИТИКА</p><h2>Результаты компании</h2><p>GEO, воронка и трафик на одном экране.</p></div><span>◈</span></div>
        <div className="exec-kpis">
          <div><span>ЛИДЫ</span><b>{stats.leads}</b></div><div><span>REG</span><b>{stats.reg}</b></div><div><span>FTD</span><b>{stats.ftd}</b></div><div><span>FT</span><b>{stats.ft}</b></div>
          <div><span>ДЕПОЗИТЫ</span><b>{effectiveUser.role === "seo" ? "Скрыто" : "$" + Number(stats.deposits || 0).toFixed(2)}</b></div>
          <div><span>СРЕДНИЙ FTD</span><b>{effectiveUser.role === "seo" ? "Скрыто" : "$" + (stats.ftd ? (Number(stats.deposits || 0)/stats.ftd).toFixed(2) : "0.00")}</b></div>
        </div>
        <div className="section-title">Результат по GEO</div>
        <div className="geo-table">{(stats.geo || []).map(x => <div className="geo-row" key={x.country}><b>{x.country}</b><span>{x.leads} лид.</span><span>{x.reg} REG</span><span>{x.ftd} FTD</span><strong>{effectiveUser.role === "seo" ? "Скрыто" : "$" + Number(x.deposits || 0).toFixed(0)}</strong></div>)}</div>
        <div className="section-title">Результат по трафику</div>
        <div className="traffic-table">{(stats.chatterfy?.attribution || []).map((x,i) => <div className="traffic-row" key={x.click_id+i}><div><b>{x.campaign}</b><small>{x.source}</small></div><span>{x.leads} лид.</span><span>{x.reg} REG</span><span>{x.ftd} FTD</span><strong>{effectiveUser.role === "seo" ? "—" : "$" + Number(x.deposits || 0).toFixed(0)}</strong></div>)}</div>
        <div className="section-title">FTD по дням (последние 14)</div>
        <div className="daily-strip">{(stats.daily || []).slice(-14).map(x => <div key={x.date}><b>{x.ftd}</b><span>FTD</span><small>{x.date.slice(5)}</small></div>)}</div>
      </section>}
      {tab === "admin" && effectiveUser.is_admin && <section className="card">
        <p className="eyebrow">КОМАНДА</p>
        <h2>Доступ команды</h2>
        <div className="team-form">
          <input value={newUser} onChange={e => setNewUser(e.target.value)} placeholder="@telegram_username" />
          <select value={newRole} onChange={e => setNewRole(e.target.value)}>
            <option value="handler">Обработчик</option>
            <option value="seo">SEO</option>
            <option value="head_buying">Хэд баинга</option>
            <option value="admin">Админ</option>
          </select>
          <button className="primary" type="button" onClick={saveTeamUser} disabled={savingUser}>{savingUser ? "Добавляю…" : "Добавить"}</button>
        </div>
        {teamMessage && <div className="team-message">{teamMessage}</div>}
        <div className="team-list">
          {team.map((member) => <div className="team-row" key={member.username}><div><b>@{member.username}</b><small>{ROLE_LABELS[member.role] || member.role}</small></div><span className={member.active ? "status-dot on" : "status-dot"}>{member.active ? "Активен" : "Отключён"} {member.username !== "jokwq" && member.username !== "nodari777" ? <button onClick={() => disableTeamUser(member.username)}>Отключить</button> : null}</span></div>)}
        </div>
      </section>}
      {loading && tab === "clients" && !openLead && <section className="card"><p>Ищу…</p></section>}
      {!loading && results.length > 1 && tab === "clients" && !openLead && <section className="card">
        <p className="eyebrow">{results.length >= 20 ? "ПЕРВЫЕ 20 СОВПАДЕНИЙ" : "НАЙДЕНО: " + results.length}</p>
        <div className="recent-list result-list">{results.map((x, i) => <button key={(x.lead_key || x.email || "") + i} className={client === x ? "selected" : ""} onClick={() => { setClient(x); setDebugInfo(null); }}><div><b>{clientTitle(x)}</b><small>{[x.tg_username && x.name ? "@" + x.tg_username : "", x.uid ? "UID " + x.uid : "", x.email && x.email !== clientTitle(x) ? x.email : "", x.country, x.campaign].filter(Boolean).join(" · ") || "—"}</small></div><span>{x.events?.at(-1)?.type || "LEAD"}</span></button>)}</div>
      </section>}
      {client && tab === "clients" && !openLead && <section className="card">
        <div className="card-title">
          <div><p className="eyebrow">КЛИЕНТ</p><h2>{clientTitle(client)}</h2></div>
          <span className="badge">{client.events?.at(-1)?.type || "LEAD"}</span>
        </div>
        <div style={{display:"flex", gap:8, flexWrap:"wrap", margin:"0 0 14px"}}>
          {["LEAD","REG","FTD","FT"].filter(tag => tag === "LEAD" || (client.events || []).some(e => String(e.type || "").toUpperCase() === tag))
            .map(tag => <span key={tag} className="badge">{tag}</span>)}
        </div>
        {client.lead_key && leadRoles.includes(effectiveUser.role) && <button className="primary open-lead" onClick={() => setOpenLead(client.lead_key)}>Открыть карточку лида →</button>}
        <div className="client-grid">
          <span>Имя</span><b>{client.name || "—"}</b>
          <span>Telegram</span><b>{client.tg_username ? "@" + client.tg_username : "—"}</b>
          <span>Email</span><b>{client.email || "—"}</b>
          <span>UID</span><b>{client.uid || "—"}</b>
          <span>Телефон</span><b>{client.phone || "—"}</b>
          <span>Broker ID</span><b>{client.broker_id || "—"}</b>
          <span>Click ID</span><b>{client.click_id || "—"}</b>
          <span>Страна</span><b>{client.country || "—"}</b>
          <span>Статус</span><b>{STATUS_LABELS[client.status] || client.status || "—"}</b>
          <span>Регистрация</span><b>{client.registration_date || "—"}</b>
          <span>FTD</span><b>{client.first_fund_date || "—"}</b>
          <span>FT</span><b>{client.first_trade_date || "—"}</b>
          <span>Депозит</span><b>{effectiveUser.role === "handler" || effectiveUser.role === "seo" ? "Скрыто" : (client.first_fund_amount != null ? "$" + client.first_fund_amount : "—")}</b>
          <span>Логин FxPro</span><b>{client.fxpro_logins?.length ? client.fxpro_logins.join(", ") : (client.fxpro_accounts?.length ? client.fxpro_accounts.map(a => a.login).join(", ") : "—")}</b>
          <span>Счетов FxPro</span><b>{client.fxpro_account_count || 0}</b>
          <span>Баланс FxPro</span><b>{effectiveUser.role === "handler" || effectiveUser.role === "seo" ? "Скрыто" : (client.latest_balance != null ? "$" + Number(client.latest_balance).toFixed(2) : "—")}</b>
        </div>
        {Object.keys(client.attribution || {}).length > 0 && <div className="attribution-card">
          <div className="section-title">Атрибуция Chatterfy</div>
          <div className="client-grid">
            <span>Кампания</span><b>{client.campaign || client.attribution.tracker_campaign || client.attribution.tracker_campaign_name || "—"}</b>
            <span>Источник</span><b>{client.source || client.attribution.tracker_source || client.attribution.tracker_source_name || "—"}</b>
            <span>AdSet</span><b>{client.adset || client.attribution.adset_name || client.attribution.adset_id || "—"}</b>
            <span>Ad</span><b>{client.ad || client.attribution.ad_id || "—"}</b>
            <span>Placement</span><b>{client.placement || client.attribution.placement || "—"}</b>
            <span>Лендинг</span><b>{client.attribution.tracker_landing_id || "—"}</b>
          </div>
          {client.chat_link && <a className="chat-link" href={client.chat_link} target="_blank" rel="noreferrer">Открыть чат в Chatterfy ↗</a>}
        </div>}
        {effectiveUser.is_admin && <div className="attribution-card">
          <div className="section-title">Диагностика связки</div>
          <button className="primary" onClick={debugClientLink} disabled={debugLoading || !client.email}>{debugLoading ? "Проверяю…" : "Проверить связку БД → API"}</button>
          {debugInfo && <div className="client-grid" style={{marginTop: 12}}>
            {debugInfo.error ? <><span>Ошибка</span><b>{debugInfo.error}</b></> : <>
              <span>Normalized email</span><b>{debugInfo.normalized_email || "—"}</b>
              <span>Broker client rows</span><b>{debugInfo.counts?.broker_clients ?? 0}</b>
              <span>Chatterfy leads</span><b>{debugInfo.counts?.chatterfy_leads ?? 0}</b>
              <span>FxPro accounts</span><b>{debugInfo.counts?.fxpro_accounts ?? 0}</b>
              <span>FxPro logins</span><b>{(debugInfo.fxpro_accounts || []).map(x => x.login).join(", ") || "—"}</b>
              <span>CRM events</span><b>{debugInfo.counts?.crm_events ?? 0}</b>
              <span>Event types</span><b>{(debugInfo.crm_events || []).map(x => x.event_type + "/" + x.source).join(", ") || "—"}</b>
            </>}
          </div>}
        </div>}
        <div className="section-title">Путь клиента</div>
        <div className="timeline">
          {(client.events || []).map((event) => <div key={event.type}><b>{event.type}</b><span>{event.date}{event.amount != null ? " · $" + event.amount : ""}</span></div>)}
        </div>
      </section>}
      {!loading && searched && !results.length && tab === "clients" && !openLead && <section className="card"><p>Ничего не нашёл. Попробуй часть имени, @username без собачки или последние цифры телефона.</p></section>}

      {tab === "traffic" && canSee("traffic") && <section className="role-dashboard traffic-dashboard">
        <div className="role-hero"><div><p className="eyebrow">ТРАФИК · CHATTERFY</p><h2>Результат по трафику</h2><p>Кампания → источник → адсет → объявление → плейсмент.</p></div><span>◉</span></div>
        <div className="buying-filters"><select value={trafficCampaign} onChange={e => setTrafficCampaign(e.target.value)}><option value="all">Все кампании</option>{[...new Set(traffic.map(x => x.campaign).filter(Boolean))].sort().map(x => <option key={x} value={x}>{x}</option>)}</select><select value={trafficSource} onChange={e => setTrafficSource(e.target.value)}><option value="all">Все источники</option>{[...new Set(traffic.map(x => x.source).filter(Boolean))].sort().map(x => <option key={x} value={x}>{x}</option>)}</select></div>
        <div className="traffic-table">{traffic.filter(x => (trafficCampaign === "all" || x.campaign === trafficCampaign) && (trafficSource === "all" || x.source === trafficSource)).map((x,i) => <div className="traffic-card-row" key={x.campaign+x.source+x.adset+x.ad+i}><div><b>{x.campaign}</b><small>{x.source} · {x.adset}</small><small>{x.ad} · {x.placement}</small></div><span>{x.leads} лид.</span><span>{x.reg} REG</span><span>{x.ftd} FTD</span><span>{x.ft} FT</span><span>{x.reg_to_ftd}%</span><strong>{"$" + Number(x.deposits || 0).toFixed(0)}</strong></div>)}</div>
      </section>}
      {tab === "operations" && canSee("operations") && <section className="role-dashboard analytics-dashboard">
        <div className="role-hero"><div><p className="eyebrow">ОПЕРАЦИИ</p><h2>Контроль CRM</h2><p>Где сходятся атрибуция, счета FxPro и события брокера.</p></div><span>⚙️</span></div>
        <div className="ops-grid">
          <div><span>Лиды Chatterfy</span><b>{operations?.counts?.chatterfy_leads ?? 0}</b></div>
          <div><span>Email из Chatterfy</span><b>{operations?.counts?.chatterfy_emails ?? 0}</b></div>
          <div><span>Связаны с FxPro</span><b>{operations?.counts?.linked_emails ?? 0}</b></div>
          <div><span>Не связаны с FxPro</span><b>{operations?.counts?.unmatched_emails ?? 0}</b></div>
          <div><span>Email в FxPro</span><b>{operations?.counts?.fxpro_emails ?? 0}</b></div>
          <div><span>События CRM</span><b>{operations?.counts?.events ?? 0}</b></div>
        </div>
        <div className="section-title">Лиды Chatterfy, которых нет в FxPro</div>
        <div className="recent-list">{(operations?.unmatched || []).map((x,i) => <div key={x.email + "-" + i}><div><b>{x.email}</b><small>{x.campaign || "—"} · {x.source || "—"}</small></div><span>{x.click_id || "Нет Click ID"}</span></div>)}{!(operations?.unmatched || []).length && <div className="empty-state">Несвязанных лидов нет.</div>}</div>
        <div className="section-title">Последние события брокера</div>
        <div className="recent-list">{(operations?.events || []).map((x,i) => <div key={x.email + "-" + x.event_type + "-" + i}><div><b>{x.event_type} · {x.email}</b><small>{x.source || "—"} · {x.broker_id || "—"}</small></div><span>{x.event_date || x.created_at || "—"}</span></div>)}{!(operations?.events || []).length && <div className="empty-state">Событий от брокера пока не было.</div>}</div>
      </section>}
      {tab === "alerts" && canSee("alerts") && <section className="role-dashboard analytics-dashboard">
        <div className="role-hero"><div><p className="eyebrow">АЛЕРТЫ</p><h2>Что требует внимания</h2><p>Проблемы, найденные в данных Chatterfy и брокера.</p></div><span>⚠️</span></div>
        <div className="ops-grid">
          <div><span>Ждут FxPro</span><b>{alerts?.counts?.pending_fxpro ?? 0}</b></div>
          <div><span>Без атрибуции</span><b>{alerts?.counts?.missing_attribution ?? 0}</b></div>
          <div><span>Несвязанные события</span><b>{alerts?.counts?.unlinked_events ?? 0}</b></div>
          <div><span>FTD без суммы</span><b>{alerts?.counts?.ftd_missing_amount ?? 0}</b></div>
          <div><span>События Chatterfy</span><b>{alerts?.counts?.broker_events ?? 0}</b></div>
        </div>
        <div className="section-title">Лиды, которых ждём в FxPro</div>
        <div className="recent-list">{(alerts?.pending_fxpro || []).map((x,i) => <div key={x.email + "-" + i}><div><b>{x.email}</b><small>{x.campaign || "—"} · {x.source || "—"}</small></div><span>{x.click_id || "Нет Click ID"}</span></div>)}{!(alerts?.pending_fxpro || []).length && <div className="empty-state">Никого не ждём.</div>}</div>
        <div className="section-title">События с проблемами</div>
        <div className="recent-list">{(alerts?.unlinked_events || []).slice(0,10).map((x,i) => <div key={x.email + "-unlinked-" + i}><div><b>{x.event_type} · {x.email}</b><small>Событие Chatterfy не связано со счётом FxPro</small></div><span>{x.event_date || x.created_at || "—"}</span></div>)}{(alerts?.ftd_missing_amount || []).slice(0,10).map((x,i) => <div key={x.email + "-amount-" + i}><div><b>FTD · {x.email}</b><small>Нет суммы депозита</small></div><span>{x.event_date || x.created_at || "—"}</span></div>)}{!(alerts?.unlinked_events || []).length && !(alerts?.ftd_missing_amount || []).length && <div className="empty-state">Проблем не найдено.</div>}</div>
      </section>}
      {tab === "finance" && canSee("finance") && <section className="role-dashboard finance-dashboard">
        <div className="role-hero"><div><p className="eyebrow">ФИНАНСЫ · FXPRO</p><h2>Депозиты и движение денег</h2><p>Финансы по счетам из последнего отчёта FxPro.</p></div><span>💰</span></div>
        <div className="exec-kpis">
          <div><span>ДЕПОЗИТЫ</span><b>{"$" + Number(finance?.deposits || 0).toFixed(2)}</b></div><div><span>ВЫВОДЫ</span><b>{"$" + Number(finance?.withdrawals || 0).toFixed(2)}</b></div><div><span>ЧИСТЫЕ ДЕПОЗИТЫ</span><b>{"$" + Number(finance?.net_deposits || 0).toFixed(2)}</b></div>
          <div><span>БАЛАНС</span><b>{"$" + Number(finance?.balance || 0).toFixed(2)}</b></div><div><span>FTD</span><b>{finance?.ftd_count || 0}</b></div><div><span>СРЕДНИЙ FTD</span><b>{"$" + Number(finance?.avg_ftd || 0).toFixed(2)}</b></div>
        </div>
        <div className="section-title">По GEO</div>
        <div className="geo-table">{(finance?.geo || []).map(x => <div className="finance-row" key={x.country}><b>{x.country}</b><span>{"$" + Number(x.deposits || 0).toFixed(0) + " деп"}</span><span>{"$" + Number(x.withdrawals || 0).toFixed(0) + " вывод"}</span><strong>{"$" + Number(x.net || 0).toFixed(0) + " чистыми"}</strong></div>)}</div>
        <div className="finance-note">Источник: отчёт FxPro по счетам · счетов: {finance?.accounts || 0}</div>
      </section>}
      {moreOpen && <div className="more-backdrop" onClick={() => setMoreOpen(false)}>
        <div className="more-sheet" onClick={e => e.stopPropagation()}>
          <p className="eyebrow">ЕЩЁ</p>
          <div className="more-grid">{roleMeta.more.map(id => <button key={id} className={tab === id ? "active" : ""} onClick={() => go(id)}><span>{TAB_ICONS[id]}</span>{TAB_LABELS[id]}</button>)}</div>
        </div>
      </div>}
      <nav style={{ gridTemplateColumns: `repeat(${roleMeta.main.length + (roleMeta.more.length ? 1 : 0)}, minmax(0, 1fr))` }}>
        {roleMeta.main.map(id => <button key={id} className={tab === id ? "active" : ""} onClick={() => go(id)}><span className="nav-icon">{TAB_ICONS[id]}</span>{id === "dashboard" && effectiveUser.role === "handler" ? "Мой день" : TAB_LABELS[id]}</button>)}
        {roleMeta.more.length > 0 && <button className={roleMeta.more.includes(tab) || moreOpen ? "active" : ""} onClick={() => setMoreOpen(o => !o)}><span className="nav-icon">☰</span>{roleMeta.more.includes(tab) ? TAB_LABELS[tab] : "Ещё"}</button>}
      </nav>
    </main>
  );
}
const TAB_LABELS = {
  dashboard: "Главная", leads: "Лиды", reconcile: "Сверка", clients: "Клиенты", stats: "Статистика",
  finance: "Финансы", traffic: "Трафик", operations: "Операции", alerts: "Алерты", admin: "Команда",
};
const TAB_ICONS = {
  dashboard: "◉", leads: "☷", reconcile: "⚖", clients: "⌕", stats: "◈",
  finance: "$", traffic: "↗", operations: "⚙", alerts: "⚠", admin: "👥",
};
const ROLE_LABELS = { admin: "Админ", head_buying: "Хэд баинга", seo: "SEO", handler: "Обработчик" };
const STATUS_LABELS = { "Chatterfy lead": "Лид Chatterfy", "FxPro account": "Счёт FxPro" };

function clientTitle(x) {
  return x.name || (x.tg_username ? "@" + x.tg_username : "") || x.email || x.uid || "Клиент";
}

createRoot(document.getElementById("root")).render(<React.StrictMode><App /></React.StrictMode>);
