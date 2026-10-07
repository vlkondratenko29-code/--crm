import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const tg = window.Telegram?.WebApp;
const demoUser = { username: "jokwq", role: "admin", is_admin: true };


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
  const [client, setClient] = useState(null);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importMessage, setImportMessage] = useState("");
  const [stats, setStats] = useState({ leads: 0, reg: 0, ftd: 0, ft: 0, deposits: 0 });
  const [finance, setFinance] = useState(null);
  const [traffic, setTraffic] = useState([]);
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
  const roleMeta = {
    admin: { label: "ADMIN", title: "Full CRM access", tabs: ["dashboard", "clients", "stats", "finance", "traffic", "admin"] },
    head_buying: { label: "HEAD BUYING", title: "Buying & performance", tabs: ["dashboard", "clients", "stats", "finance", "traffic"] },
    seo: { label: "SEO", title: "Traffic & funnel", tabs: ["dashboard", "clients", "stats"] },
    handler: { label: "HANDLER", title: "Client operations", tabs: ["dashboard", "clients"] },
  }[effectiveUser.role] || { label: "HANDLER", title: "Client operations", tabs: ["dashboard", "clients"] };
  const canSee = (id) => roleMeta.tabs.includes(id);
  const buyingRows = useMemo(() => (stats.chatterfy?.attribution || []).filter(x => (buyingCampaign === "all" || x.campaign === buyingCampaign) && (buyingSource === "all" || x.source === buyingSource)), [stats.chatterfy?.attribution, buyingCampaign, buyingSource]);
  const buyingTotals = useMemo(() => buyingRows.reduce((a, x) => ({ leads: a.leads + Number(x.leads || 0), reg: a.reg + Number(x.reg || 0), ftd: a.ftd + Number(x.ftd || 0), ft: a.ft + Number(x.ft || 0), deposits: a.deposits + Number(x.deposits || 0) }), { leads: 0, reg: 0, ftd: 0, ft: 0, deposits: 0 }), [buyingRows]);

  React.useEffect(() => {
    tg?.ready?.();
    tg?.expand?.();
    const loadMe = async () => {
      try {
        const base = import.meta.env.VITE_API_URL || "";
        const response = await fetch(base + "/api/v1/me", {
          headers: { "X-Telegram-Username": effectiveUser.username || "jokwq" }
        });
        if (response.ok) setRoleInfo(await response.json());
      } catch (error) {
        console.error(error);
      }
    };
    const loadDashboard = async () => {
      try {
        const base = import.meta.env.VITE_API_URL || "";
        const response = await fetch(`${base}/api/v1/dashboard`, {
          headers: { "X-Telegram-Username": effectiveUser.username || "jokwq" }
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
    loadTeam();
  }, [user.username]);

  React.useEffect(() => {
    if (effectiveUser.is_admin) loadTeam();
  }, [effectiveUser.username, effectiveUser.is_admin]);

  React.useEffect(() => { if (canSee("finance")) loadFinance(); if (canSee("traffic")) loadTraffic(); }, [effectiveUser.username, effectiveUser.role, tab]);

  async function loadTraffic() { if (!canSee("traffic")) return; try { const base = import.meta.env.VITE_API_URL || ""; const res = await fetch(base + "/api/v1/traffic", { headers: { "X-Telegram-Username": effectiveUser.username || "jokwq" } }); if (res.ok) setTraffic((await res.json()).rows || []); } catch (error) { console.error(error); } }

  async function loadFinance() { if (!["admin", "head_buying"].includes(effectiveUser.role)) return; try { const base = import.meta.env.VITE_API_URL || ""; const res = await fetch(base + "/api/v1/finance", { headers: { "X-Telegram-Username": effectiveUser.username || "jokwq" } }); if (res.ok) setFinance(await res.json()); } catch (error) { console.error(error); } }

  async function loadTeam() {
    if (!effectiveUser.is_admin) return;
    const base = import.meta.env.VITE_API_URL || "";
    const res = await fetch(base + "/api/v1/users", { headers: { "X-Telegram-Username": effectiveUser.username || "jokwq" } });
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
        headers: { "Content-Type": "application/json", "X-Telegram-Username": effectiveUser.username || "jokwq" },
        body: JSON.stringify({ username, role: newRole, active: true })
      });
      clearTimeout(timer);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || ("Ошибка сервера: HTTP " + res.status));
      setNewUser("");
      setTeamMessage("@" + username + " добавлен как " + newRole);
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
      headers: { "X-Telegram-Username": effectiveUser.username || "jokwq" }
    });
    if (res.ok) await loadTeam();
  }

  async function search() {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const base = import.meta.env.VITE_API_URL || "";
      const response = await fetch(`${base}/api/v1/clients/search?q=${encodeURIComponent(query)}`, {
        headers: { "X-Telegram-Username": effectiveUser.username || "jokwq" }
      });
      if (!response.ok) throw new Error("Search failed");
      const data = await response.json();
      setClient(data.clients?.[0] || null);
    } catch (error) {
      console.error(error);
      setClient(null);
    } finally {
      setLoading(false);
    }
  }

  function NavButton({ id, children }) {
    return <button className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{children}</button>;
  }

  return (
    <main className={"app role-" + effectiveUser.role}>
      <header>
        <div><p className="eyebrow">BROKER CRM</p><h1>Dashboard</h1><p className="subtitle">Client operations · FxPro · Chatterfy · Build 08.10</p></div>
        <div className="avatar">{effectiveUser.username?.[0]?.toUpperCase() || "?"}</div>
      </header>

      <section className="userbar">
        <span>@{effectiveUser.username || "telegram-user"}</span>
        <strong>{roleMeta.label}</strong>
      </section>

      {effectiveUser.is_admin && <section className="admin-card">
        <p className="eyebrow">ADMIN ACCESS</p>
        <h2>Full CRM access enabled</h2>
        <p>{roleMeta.title} · Clients · Analytics · Team · Settings</p>
        <div className="role-preview">
          <span>Preview role</span>
          <button className={!previewRole ? "selected" : ""} onClick={() => setPreviewRole("")}>Admin</button>
          <button className={previewRole === "head_buying" ? "selected" : ""} onClick={() => setPreviewRole("head_buying")}>Head of Buying</button>
          <button className={previewRole === "seo" ? "selected" : ""} onClick={() => setPreviewRole("seo")}>SEO</button>
          <button className={previewRole === "handler" ? "selected" : ""} onClick={() => setPreviewRole("handler")}>Handler</button>
        </div>
        {previewRole && <div className="preview-note">Preview only — your real Admin access is unchanged.</div>}
        <label className="upload-btn">
          {importing ? "Uploading..." : "Upload FxPro reports"}
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
                headers: { "X-Telegram-Username": effectiveUser.username || "jokwq" }
              });
              const responseText = await res.text();
              let data;
              try {
                data = JSON.parse(responseText);
              } catch {
                throw new Error(responseText.replace(/<[^>]*>/g, " ").replace(/\\s+/g, " ").trim().slice(0, 300) || `Import failed (HTTP ${res.status})`);
              }
              if (!res.ok) throw new Error(data.detail || "Import failed");
              setImportMessage(`Imported ${data.client_rows || 0} clients + ${data.account_rows || 0} FxPro accounts · linked by email: ${data.email_linked_clients || 0} clients · ${data.files} reports`);
              const dashboard = await fetch(`${base}/api/v1/dashboard`, {
                headers: { "X-Telegram-Username": effectiveUser.username || "jokwq" }
              });
              if (dashboard.ok) setStats(await dashboard.json());
            } catch (err) {
              setImportMessage(err.message);
            } finally { setImporting(false); e.target.value = ""; }
          }} />
        </label>
        {importMessage && <p>{importMessage}</p>}
      </section>}

      {tab === "dashboard" && effectiveUser.role === "admin" && <section className="role-dashboard admin-dashboard">
        <div className="role-hero"><div><p className="eyebrow">EXECUTIVE CONTROL CENTER</p><h2>One place for the whole team</h2><p>Traffic → clients → deposits → performance.</p></div><span>👑</span></div>
        <div className="exec-kpis">
          <div><span>LEADS</span><b>{stats.leads}</b></div><div><span>REG</span><b>{stats.reg}</b></div><div><span>FTD</span><b>{stats.ftd}</b></div><div><span>FT</span><b>{stats.ft}</b></div>
          <div><span>DEPOSITS</span><b>{"$" + Number(stats.deposits || 0).toFixed(2)}</b></div><div><span>NET DEPOSITS</span><b>{"$" + Number(stats.company?.[0]?.net_deposits || 0).toFixed(2)}</b></div>
        </div>
        <div className="section-title">Funnel health</div>
        <div className="mini-metrics"><div><span>REG → FTD</span><b>{stats.funnel?.reg_to_ftd ?? 0}%</b></div><div><span>FTD → FT</span><b>{stats.funnel?.ftd_to_ft ?? 0}%</b></div><div><span>REG → FT</span><b>{stats.funnel?.reg_to_ft ?? 0}%</b></div></div>
        <div className="section-title">GEO performance</div>
        <div className="geo-table">{(stats.geo || []).slice(0,6).map(x => <div className="geo-row" key={x.country}><b>{x.country}</b><span>{x.reg} REG</span><span>{x.ftd} FTD</span><strong>{"$" + Number(x.deposits || 0).toFixed(0)}</strong></div>)}</div>
        <div className="section-title">Operations</div>
        <div className="ops-grid">
          <div><span>Chatterfy matched</span><b>{stats.operations?.attributed_clients ?? 0}</b></div>
          <div><span>Need attribution</span><b>{stats.operations?.unattributed_clients ?? 0}</b></div>
          <div><span>FxPro accounts</span><b>{stats.operations?.fxpro_accounts ?? 0}</b></div>
        </div>
      </section>}
      {tab === "dashboard" && effectiveUser.role === "head_buying" && <section className="role-dashboard buying-dashboard">
        <div className="role-hero"><div><p className="eyebrow">BUYING · CHATTERFY</p><h2>Buying performance</h2><p>Chatterfy is the attribution tracker · performance by Click ID.</p></div><span>📊</span></div>
        <div className="funnel"><div><span>LEADS</span><b>{buyingTotals.leads}</b></div><i>→</i><div><span>REG</span><b>{buyingTotals.reg}</b></div><i>→</i><div><span>FTD</span><b>{buyingTotals.ftd}</b></div><i>→</i><div><span>FT</span><b>{buyingTotals.ft}</b></div></div>
        <div className="mini-metrics"><div><span>REG → FTD</span><b>{buyingTotals.reg ? (buyingTotals.ftd / buyingTotals.reg * 100).toFixed(1) : "0.0"}%</b></div><div><span>FTD → FT</span><b>{buyingTotals.ftd ? (buyingTotals.ft / buyingTotals.ftd * 100).toFixed(1) : "0.0"}%</b></div><div><span>Avg FTD</span><b>{buyingTotals.ftd ? (buyingTotals.deposits / buyingTotals.ftd).toFixed(2) : "0.00"}</b></div></div>
        <div className="buying-filters"><select value={buyingCampaign} onChange={e => setBuyingCampaign(e.target.value)}><option value="all">All campaigns</option>{[...new Set((stats.chatterfy?.attribution || []).map(x => x.campaign).filter(Boolean))].sort().map(x => <option key={x} value={x}>{x}</option>)}</select><select value={buyingSource} onChange={e => setBuyingSource(e.target.value)}><option value="all">All sources</option>{[...new Set((stats.chatterfy?.attribution || []).map(x => x.source).filter(Boolean))].sort().map(x => <option key={x} value={x}>{x}</option>)}</select></div>
        <div className="section-title">Chatterfy · Campaign performance</div>
        <div className="buying-summary">
          <div><span>Attributed leads</span><b>{buyingTotals.leads}</b></div>
          <div><span>Attributed FTD</span><b>{buyingTotals.ftd}</b></div>
          <div><span>Attributed deposits</span><b>${buyingTotals.deposits.toFixed(2)}</b></div>
        </div>
        <div className="attr-header"><span>CAMPAIGN / SOURCE</span><span>ADSET · AD · PLACEMENT</span><span>RESULT</span></div>
        <div className="click-table attribution-table">
          {buyingRows.map((x, i) => <div className="attribution-row" key={x.click_id + "-" + i}>
            <div className="attr-main"><b>{x.campaign}</b><small>{x.source}</small><em>{x.click_id}</em></div>
            <div className="attr-meta"><span>{x.adset}</span><span>{x.ad}</span><span>{x.placement}</span></div>
            <div className="attr-stats"><b>{x.leads}</b><span>LEAD</span><strong>{x.ftd} FTD</strong><span>${Number(x.deposits || 0).toFixed(2)}</span></div>
          </div>)}
          {!(stats.chatterfy?.attribution || []).length && <div className="empty-state">Chatterfy attribution will appear after a matched FxPro client is received.</div>}
        </div>
        <div className="section-title">Chatterfy · Top Click IDs</div>
        <div className="click-table">{(stats.chatterfy?.clicks || []).map(x => <div className="click-row" key={x.click_id}><span className="click-id">{x.click_id}</span><b>{x.leads}</b><span>{x.reg_to_ftd}% FTD</span><strong>{x.ftd} FTD</strong></div>)}</div>
        <div className="section-title">Top countries</div><div className="country-list">{(stats.top_countries || []).map(x => <div key={x.country}><span>{x.country}</span><b>{x.count}</b></div>)}</div>
      </section>}
      {tab === "dashboard" && effectiveUser.role === "seo" && <section className="role-dashboard seo-dashboard">
        <div className="role-hero"><div><p className="eyebrow">SEO</p><h2>Traffic & funnel</h2><p>Lead volume and conversion without financial data.</p></div><span>📈</span></div>
        <div className="mini-metrics"><div><span>Leads</span><b>{stats.leads}</b></div><div><span>REG → FTD</span><b>{stats.funnel?.reg_to_ftd ?? 0}%</b></div><div><span>REG → FT</span><b>{stats.funnel?.reg_to_ft ?? 0}%</b></div></div>
        <div className="section-title">Top countries</div><div className="country-list">{(stats.top_countries || []).map(x => <div key={x.country}><span>{x.country}</span><b>{x.count}</b></div>)}</div>
      </section>}
      {tab === "dashboard" && effectiveUser.role === "handler" && <section className="role-dashboard handler-dashboard">
        <div className="role-hero"><div><p className="eyebrow">HANDLER</p><h2>Client work</h2><p>Recent registrations ready for processing.</p></div><span>🎧</span></div>
        <div className="mini-metrics"><div><span>New leads</span><b>{stats.leads}</b></div><div><span>Registered</span><b>{stats.reg}</b></div><div><span>FTD</span><b>{stats.ftd}</b></div></div>
        <div className="section-title">Recent clients</div><div className="recent-list">{(stats.recent || []).map(x => <div key={x.email}><div><b>{x.email}</b><small>{x.country || "—"} · {x.status || "—"}</small></div><span>{x.registration_date || "—"}</span></div>)}</div>
      </section>}

      {tab === "dashboard" && <section className="search-card">
        <h2>Find client</h2>
        <div className="search">
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Email or Click ID" />
          <button onClick={search}>Search</button>
        </div>
      </section>}

      {tab === "clients" && <section className="search-card">
        <h2>Find client</h2>
        <div className="search">
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Email or Click ID" />
          <button onClick={search}>Search</button>
        </div>
      </section>}
      {tab === "stats" && canSee("stats") && <section className="role-dashboard analytics-dashboard">
        <div className="role-hero"><div><p className="eyebrow">ANALYTICS HUB</p><h2>Company performance</h2><p>GEO, funnel and traffic in one view.</p></div><span>◈</span></div>
        <div className="exec-kpis">
          <div><span>LEADS</span><b>{stats.leads}</b></div><div><span>REG</span><b>{stats.reg}</b></div><div><span>FTD</span><b>{stats.ftd}</b></div><div><span>FT</span><b>{stats.ft}</b></div>
          <div><span>DEPOSITS</span><b>{effectiveUser.role === "seo" ? "Hidden" : "$" + Number(stats.deposits || 0).toFixed(2)}</b></div>
          <div><span>AVG FTD</span><b>{effectiveUser.role === "seo" ? "Hidden" : "$" + (stats.ftd ? (Number(stats.deposits || 0)/stats.ftd).toFixed(2) : "0.00")}</b></div>
        </div>
        <div className="section-title">GEO performance</div>
        <div className="geo-table">{(stats.geo || []).map(x => <div className="geo-row" key={x.country}><b>{x.country}</b><span>{x.leads} leads</span><span>{x.reg} REG</span><span>{x.ftd} FTD</span><strong>{effectiveUser.role === "seo" ? "Hidden" : "$" + Number(x.deposits || 0).toFixed(0)}</strong></div>)}</div>
        <div className="section-title">Traffic performance</div>
        <div className="traffic-table">{(stats.chatterfy?.attribution || []).map((x,i) => <div className="traffic-row" key={x.click_id+i}><div><b>{x.campaign}</b><small>{x.source}</small></div><span>{x.leads} leads</span><span>{x.reg} REG</span><span>{x.ftd} FTD</span><strong>{effectiveUser.role === "seo" ? "—" : "$" + Number(x.deposits || 0).toFixed(0)}</strong></div>)}</div>
        <div className="section-title">30-day activity</div>
        <div className="daily-strip">{(stats.daily || []).slice(-14).map(x => <div key={x.date}><b>{x.ftd}</b><span>FTD</span><small>{x.date.slice(5)}</small></div>)}</div>
      </section>}
      {tab === "admin" && effectiveUser.is_admin && <section className="card">
        <p className="eyebrow">TEAM</p>
        <h2>Team access</h2>
        <div className="team-form">
          <input value={newUser} onChange={e => setNewUser(e.target.value)} placeholder="@telegram_username" />
          <select value={newRole} onChange={e => setNewRole(e.target.value)}>
            <option value="handler">Handler</option>
            <option value="seo">SEO</option>
            <option value="head_buying">Head of Buying</option>
            <option value="admin">Admin</option>
          </select>
          <button className="primary" type="button" onClick={saveTeamUser} disabled={savingUser}>{savingUser ? "Adding…" : "Add member"}</button>
        </div>
        {teamMessage && <div className="team-message">{teamMessage}</div>}
        <div className="team-list">
          {team.map((member) => <div className="team-row" key={member.username}><div><b>@{member.username}</b><small>{member.role.replace("_", " ")}</small></div><span className={member.active ? "status-dot on" : "status-dot"}>{member.active ? "Active" : "Off"} {member.username !== "jokwq" && member.username !== "nodari777" ? <button onClick={() => disableTeamUser(member.username)}>Disable</button> : null}</span></div>)}
        </div>
      </section>}
      {loading && <section className="card"><p>Searching...</p></section>}
      {client && <section className="card">
        <div className="card-title">
          <div><p className="eyebrow">CLIENT</p><h2>{client.email}</h2></div>
          <span className="badge">{client.events?.at(-1)?.type || "LEAD"}</span>
        </div>
        <div className="client-grid">
          <span>Broker ID</span><b>{client.broker_id || "—"}</b>
          <span>Click ID</span><b>{client.click_id || "—"}</b>
          <span>Country</span><b>{client.country || "—"}</b>
          <span>Status</span><b>{client.status || "—"}</b>
          <span>Registration</span><b>{client.registration_date || "—"}</b>
          <span>FTD</span><b>{client.first_fund_date || "—"}</b>
          <span>FT</span><b>{client.first_trade_date || "—"}</b>
          <span>Deposit</span><b>{effectiveUser.role === "handler" || effectiveUser.role === "seo" ? "Hidden" : (client.first_fund_amount != null ? "$" + client.first_fund_amount : "—")}</b>
          <span>FxPro Login</span><b>{client.fxpro_logins?.length ? client.fxpro_logins.join(", ") : (client.fxpro_accounts?.length ? client.fxpro_accounts.map(a => a.login).join(", ") : "—")}</b>
          <span>FxPro Accounts</span><b>{client.fxpro_account_count || 0}</b>
          <span>FxPro Balance</span><b>{effectiveUser.role === "handler" || effectiveUser.role === "seo" ? "Hidden" : (client.latest_balance != null ? "$" + Number(client.latest_balance).toFixed(2) : "—")}</b>
        </div>
        {Object.keys(client.attribution || {}).length > 0 && <div className="attribution-card">
          <div className="section-title">Chatterfy attribution</div>
          <div className="client-grid">
            <span>Campaign</span><b>{client.campaign || client.attribution.tracker_campaign || client.attribution.tracker_campaign_name || "—"}</b>
            <span>Source</span><b>{client.source || client.attribution.tracker_source || client.attribution.tracker_source_name || "—"}</b>
            <span>AdSet</span><b>{client.adset || client.attribution.adset_name || client.attribution.adset_id || "—"}</b>
            <span>Ad</span><b>{client.ad || client.attribution.ad_id || "—"}</b>
            <span>Placement</span><b>{client.placement || client.attribution.placement || "—"}</b>
            <span>Landing</span><b>{client.attribution.tracker_landing_id || "—"}</b>
          </div>
          {client.chat_link && <a className="chat-link" href={client.chat_link} target="_blank" rel="noreferrer">Open Chatterfy chat ↗</a>}
        </div>}
        <div className="section-title">Client Journey</div>
        <div className="timeline">
          {(client.events || []).map((event) => <div key={event.type}><b>{event.type}</b><span>{event.date}{event.amount != null ? " · $" + event.amount : ""}</span></div>)}
        </div>
      </section>}
      {!loading && !client && query && <section className="card"><p>No client found.</p></section>}

      {tab === "traffic" && canSee("traffic") && <section className="role-dashboard traffic-dashboard">
        <div className="role-hero"><div><p className="eyebrow">TRAFFIC ANALYTICS · CHATTERFY</p><h2>Traffic performance</h2><p>Campaign → Source → AdSet → Ad → Placement.</p></div><span>◉</span></div>
        <div className="buying-filters"><select value={trafficCampaign} onChange={e => setTrafficCampaign(e.target.value)}><option value="all">All campaigns</option>{[...new Set(traffic.map(x => x.campaign).filter(Boolean))].sort().map(x => <option key={x} value={x}>{x}</option>)}</select><select value={trafficSource} onChange={e => setTrafficSource(e.target.value)}><option value="all">All sources</option>{[...new Set(traffic.map(x => x.source).filter(Boolean))].sort().map(x => <option key={x} value={x}>{x}</option>)}</select></div>
        <div className="traffic-table">{traffic.filter(x => (trafficCampaign === "all" || x.campaign === trafficCampaign) && (trafficSource === "all" || x.source === trafficSource)).map((x,i) => <div className="traffic-card-row" key={x.campaign+x.source+x.adset+x.ad+i}><div><b>{x.campaign}</b><small>{x.source} · {x.adset}</small><small>{x.ad} · {x.placement}</small></div><span>{x.leads} leads</span><span>{x.reg} REG</span><span>{x.ftd} FTD</span><span>{x.ft} FT</span><span>{x.reg_to_ftd}%</span><strong>{"$" + Number(x.deposits || 0).toFixed(0)}</strong></div>)}</div>
      </section>}
      {tab === "finance" && canSee("finance") && <section className="role-dashboard finance-dashboard">
        <div className="role-hero"><div><p className="eyebrow">FINANCE · FXPRO</p><h2>Deposits & cash flow</h2><p>Account-level financial performance from the latest FxPro report.</p></div><span>💰</span></div>
        <div className="exec-kpis">
          <div><span>DEPOSITS</span><b>{"$" + Number(finance?.deposits || 0).toFixed(2)}</b></div><div><span>WITHDRAWALS</span><b>{"$" + Number(finance?.withdrawals || 0).toFixed(2)}</b></div><div><span>NET DEPOSITS</span><b>{"$" + Number(finance?.net_deposits || 0).toFixed(2)}</b></div>
          <div><span>LIVE BALANCE</span><b>{"$" + Number(finance?.balance || 0).toFixed(2)}</b></div><div><span>FTD</span><b>{finance?.ftd_count || 0}</b></div><div><span>AVG FTD</span><b>{"$" + Number(finance?.avg_ftd || 0).toFixed(2)}</b></div>
        </div>
        <div className="section-title">By GEO</div>
        <div className="geo-table">{(finance?.geo || []).map(x => <div className="finance-row" key={x.country}><b>{x.country}</b><span>{"$" + Number(x.deposits || 0).toFixed(0) + " dep"}</span><span>{"$" + Number(x.withdrawals || 0).toFixed(0) + " wd"}</span><strong>{"$" + Number(x.net || 0).toFixed(0) + " net"}</strong></div>)}</div>
        <div className="finance-note">Source: FxPro account report · {finance?.accounts || 0} accounts</div>
      </section>}
      <nav>
        <NavButton id="dashboard">Dashboard</NavButton>
        <NavButton id="clients">Clients</NavButton>
        {canSee("stats") && <NavButton id="stats">Stats</NavButton>}
        {canSee("finance") && <NavButton id="finance">Finance</NavButton>}
        {canSee("traffic") && <NavButton id="traffic">Traffic</NavButton>}
        {effectiveUser.is_admin && <NavButton id="admin">Admin</NavButton>}
      </nav>
    </main>
  );
}
createRoot(document.getElementById("root")).render(<React.StrictMode><App /></React.StrictMode>);
