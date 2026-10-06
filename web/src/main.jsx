import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const tg = window.Telegram?.WebApp;
const demoUser = { username: "jokwq", role: "admin", is_admin: true };

const stats = [
  ["Leads", "184"], ["REG", "121"], ["FTD", "47"], ["FT", "31"], ["Deposits", "$12,840"],
];

function App() {
  const telegramUser = tg?.initDataUnsafe?.user;
  const user = useMemo(() => {
    if (!telegramUser) return demoUser;
    const username = telegramUser.username || "";
    const admin = ["jokwq", "Nodari777"].includes(username.toLowerCase());
    return { username, role: admin ? "admin" : "handler", is_admin: admin };
  }, [telegramUser]);
  const [query, setQuery] = useState("");

  function search() {
    // Real client search will be connected after FxPro CSV import.
    alert(query ? `Search: ${query}` : "Enter email or Click ID");
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
      </section>}

      <section className="stats">
        {stats.map(([label, value]) => <article className="stat" key={label}><span>{label}</span><strong>{value}</strong></article>)}
      </section>

      <section className="search-card">
        <h2>Find client</h2>
        <div className="search">
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Email or Click ID" />
          <button onClick={search}>Search</button>
        </div>
      </section>

      <section className="card">
        <div className="card-title">
          <div><p className="eyebrow">RECENT CONVERSION</p><h2>gabrielrcorrea@hotmail.com</h2></div>
          <span className="badge">FTD</span>
        </div>
        <div className="timeline">
          <div><b>REG</b><span>13.08.2026</span></div>
          <div><b>FTD</b><span>16.08.2026 · $247.84</span></div>
          <div><b>FT</b><span>17.08.2026</span></div>
        </div>
      </section>

      <nav><button className="active">Dashboard</button><button>Clients</button><button>Stats</button><button>{user.is_admin ? "Admin" : "Profile"}</button></nav>
    </main>
  );
}
createRoot(document.getElementById("root")).render(<React.StrictMode><App /></React.StrictMode>);
