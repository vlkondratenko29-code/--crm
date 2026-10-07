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
  const [tab, setTab] = useState("dashboard");
  const [team, setTeam] = useState([]);
  const [newUser, setNewUser] = useState("");
  const [newRole, setNewRole] = useState("handler");
  const [teamMessage, setTeamMessage] = useState("");
  const [savingUser, setSavingUser] = useState(false);
  const effectiveUser = roleInfo ? { ...user, ...roleInfo, is_admin: roleInfo.role === "admin" } : user;
  const roleMeta = {
    admin: { label: "ADMIN", title: "Full CRM access", tabs: ["dashboard", "clients", "stats", "admin"] },
    head_buying: { label: "HEAD BUYING", title: "Buying & performance", tabs: ["dashboard", "clients", "stats"] },
    seo: { label: "SEO", title: "Traffic & funnel", tabs: ["dashboard", "clients", "stats"] },
    handler: { label: "HANDLER", title: "Client operations", tabs: ["dashboard", "clients"] },
  }[effectiveUser.role] || { label: "HANDLER", title: "Client operations", tabs: ["dashboard", "clients"] };
  const canSee = (id) => roleMeta.tabs.includes(id);

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
        <label className="upload-btn">
          {importing ? "Uploading..." : "Upload FxPro CSV"}
          <input type="file" accept=".csv,text/csv" disabled={importing} onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            setImporting(true); setImportMessage("");
            try {
              const base = import.meta.env.VITE_API_URL || "";
              const form = new FormData(); form.append("file", file);
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
              setImportMessage(`Imported ${data.rows} clients`);
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
        <div className="role-hero"><div><p className="eyebrow">EXECUTIVE</p><h2>CRM overview</h2><p>Full funnel, revenue and team control.</p></div><span>👑</span></div>
        <div className="mini-metrics"><div><span>Conversion REG → FTD</span><b>{stats.funnel?.reg_to_ftd ?? 0}%</b></div><div><span>FTD → FT</span><b>{stats.funnel?.ftd_to_ft ?? 0}%</b></div><div><span>REG → FT</span><b>{stats.funnel?.reg_to_ft ?? 0}%</b></div></div>
      </section>}
      {tab === "dashboard" && effectiveUser.role === "head_buying" && <section className="role-dashboard buying-dashboard">
        <div className="role-hero"><div><p className="eyebrow">BUYING · CHATTERFY</p><h2>Buying performance</h2><p>Chatterfy is the attribution tracker · performance by Click ID.</p></div><span>📊</span></div>
        <div className="funnel"><div><span>LEADS</span><b>{stats.leads}</b></div><i>→</i><div><span>REG</span><b>{stats.reg}</b></div><i>→</i><div><span>FTD</span><b>{stats.ftd}</b></div><i>→</i><div><span>FT</span><b>{stats.ft}</b></div></div>
        <div className="mini-metrics"><div><span>REG → FTD</span><b>{stats.funnel?.reg_to_ftd ?? 0}%</b></div><div><span>FTD → FT</span><b>{stats.funnel?.ftd_to_ft ?? 0}%</b></div><div><span>Avg FTD</span><b>{stats.ftd ? (Number(stats.deposits || 0) / stats.ftd).toFixed(2) : "0.00"}</b></div></div>
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
      {tab === "stats" && canSee("stats") && <section className="card">
        <p className="eyebrow">ANALYTICS</p>
        <h2>CRM Statistics</h2>
        <div className="client-grid">
          <span>Leads</span><b>{stats.leads}</b>
          <span>REG</span><b>{stats.reg}</b>
          <span>FTD</span><b>{stats.ftd}</b>
          <span>FT</span><b>{stats.ft}</b>
          <span>Deposits</span><b>{effectiveUser.role === "seo" ? "Hidden" : Number(stats.deposits || 0).toFixed(2)}</b>
        </div>
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
          <span>Deposit</span><b>{effectiveUser.role === "handler" || effectiveUser.role === "seo" ? "Hidden" : (client.first_fund_amount != null ? "$" + client.first_fund_amount : "—")}</b>
        </div>
        <div className="timeline">
          {(client.events || []).map((event) => <div key={event.type}><b>{event.type}</b><span>{event.date}{event.amount != null ? " · $" + event.amount : ""}</span></div>)}
        </div>
      </section>}
      {!loading && !client && query && <section className="card"><p>No client found.</p></section>}

      <nav>
        <NavButton id="dashboard">Dashboard</NavButton>
        <NavButton id="clients">Clients</NavButton>
        {canSee("stats") && <NavButton id="stats">Stats</NavButton>}
        {effectiveUser.is_admin && <NavButton id="admin">Admin</NavButton>}
      </nav>
    </main>
  );
}
createRoot(document.getElementById("root")).render(<React.StrictMode><App /></React.StrictMode>);
