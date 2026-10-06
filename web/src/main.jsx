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
  const [client, setClient] = useState(null);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importMessage, setImportMessage] = useState("");
  const [stats, setStats] = useState({ leads: 0, reg: 0, ftd: 0, ft: 0, deposits: 0 });
  const [tab, setTab] = useState("dashboard");
  const [team, setTeam] = useState([]);
  const [newUser, setNewUser] = useState("");
  const [newRole, setNewRole] = useState("handler");

  React.useEffect(() => {
    tg?.ready?.();
    tg?.expand?.();
    const loadDashboard = async () => {
      try {
        const base = import.meta.env.VITE_API_URL || "";
        const response = await fetch(`${base}/api/v1/dashboard`, {
          headers: { "X-Telegram-Username": user.username || "jokwq" }
        });
        if (!response.ok) return;
        const data = await response.json();
        setStats(data);
      } catch (error) {
        console.error(error);
      }
    };
    loadDashboard();
    loadTeam();
  }, [user.username]);

  async function loadTeam() {
    if (!user.is_admin) return;
    const base = import.meta.env.VITE_API_URL || "";
    const res = await fetch(base + "/api/v1/users", { headers: { "X-Telegram-Username": user.username || "jokwq" } });
    if (res.ok) setTeam((await res.json()).users || []);
  }

  async function saveTeamUser() {
    if (!newUser.trim()) return;
    const base = import.meta.env.VITE_API_URL || "";
    const res = await fetch(base + "/api/v1/users", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Telegram-Username": user.username || "jokwq" },
      body: JSON.stringify({ username: newUser.trim(), role: newRole, active: true })
    });
    if (res.ok) { setNewUser(""); await loadTeam(); }
  }

  async function disableTeamUser(username) {
    const base = import.meta.env.VITE_API_URL || "";
    const res = await fetch(base + "/api/v1/users/" + encodeURIComponent(username), {
      method: "DELETE",
      headers: { "X-Telegram-Username": user.username || "jokwq" }
    });
    if (res.ok) await loadTeam();
  }

  async function search() {
    if (!query.trim()) return;
    setLoading(true);
    try {
      const base = import.meta.env.VITE_API_URL || "";
      const response = await fetch(`${base}/api/v1/clients/search?q=${encodeURIComponent(query)}`, {
        headers: { "X-Telegram-Username": user.username || "jokwq" }
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
    <main className="app">
      <header>
        <div><p className="eyebrow">BROKER CRM</p><h1>Dashboard</h1></div>
        <div className="avatar">{user.username?.[0]?.toUpperCase() || "?"}</div>
      </header>

      <section className="userbar">
        <span>@{user.username || "telegram-user"}</span>
        <strong>{user.role === "admin" ? "ADMIN" : "HANDLER"}</strong>
      </section>

      {user.is_admin && <section className="admin-card">
        <p className="eyebrow">ADMIN ACCESS</p>
        <h2>Full CRM access enabled</h2>
        <p>Clients · Analytics · Team · Settings</p>
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
                headers: { "X-Telegram-Username": user.username || "jokwq" }
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
                headers: { "X-Telegram-Username": user.username || "jokwq" }
              });
              if (dashboard.ok) setStats(await dashboard.json());
            } catch (err) {
              setImportMessage(err.message);
            } finally { setImporting(false); e.target.value = ""; }
          }} />
        </label>
        {importMessage && <p>{importMessage}</p>}
      </section>}

      {tab === "dashboard" && <section className="stats">
        {[
          ["Leads", stats.leads],
          ["REG", stats.reg],
          ["FTD", stats.ftd],
          ["FT", stats.ft],
          ["Deposits", `${Number(stats.deposits || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`],
        ].map(([label, value]) => <article className="stat" key={label}><span>{label}</span><strong>{value}</strong></article>)}
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
      {tab === "stats" && <section className="card">
        <p className="eyebrow">ANALYTICS</p>
        <h2>CRM Statistics</h2>
        <div className="client-grid">
          <span>Leads</span><b>{stats.leads}</b>
          <span>REG</span><b>{stats.reg}</b>
          <span>FTD</span><b>{stats.ftd}</b>
          <span>FT</span><b>{stats.ft}</b>
          <span>Deposits</span><b>{Number(stats.deposits || 0).toFixed(2)}</b>
        </div>
      </section>}
      {tab === "admin" && user.is_admin && <section className="card">
        <p className="eyebrow">TEAM</p>
        <h2>Team access</h2>
        <div className="search">
          <input value={newUser} onChange={e => setNewUser(e.target.value)} placeholder="@telegram_username" />
          <select value={newRole} onChange={e => setNewRole(e.target.value)}>
            <option value="handler">Handler</option>
            <option value="seo">SEO</option>
            <option value="head_buying">Head of Buying</option>
            <option value="admin">Admin</option>
          </select>
          <button onClick={saveTeamUser}>Add</button>
        </div>
        <div className="timeline">
          {team.map((member) => <div key={member.username}><b>@{member.username}</b><span>{member.role} · {member.active ? "active" : "off"} {member.username !== "jokwq" && member.username !== "nodari777" ? <button onClick={() => disableTeamUser(member.username)}>Disable</button> : null}</span></div>)}
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
          <span>Deposit</span><b>{client.first_fund_amount != null ? "$" + client.first_fund_amount : "—"}</b>
        </div>
        <div className="timeline">
          {(client.events || []).map((event) => <div key={event.type}><b>{event.type}</b><span>{event.date}{event.amount != null ? " · $" + event.amount : ""}</span></div>)}
        </div>
      </section>}
      {!loading && !client && query && <section className="card"><p>No client found.</p></section>}

      <nav>
        <NavButton id="dashboard">Dashboard</NavButton>
        <NavButton id="clients">Clients</NavButton>
        <NavButton id="stats">Stats</NavButton>
        <NavButton id="admin">{user.is_admin ? "Admin" : "Profile"}</NavButton>
      </nav>
    </main>
  );
}
createRoot(document.getElementById("root")).render(<React.StrictMode><App /></React.StrictMode>);
