import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const stats = [
  ["Leads", "184"],
  ["REG", "121"],
  ["FTD", "47"],
  ["FT", "31"],
  ["Deposits", "$12,840"],
];

function App() {
  return (
    <main className="app">
      <header>
        <div>
          <p className="eyebrow">BROKER CRM</p>
          <h1>Dashboard</h1>
        </div>
        <div className="avatar">A</div>
      </header>

      <section className="stats">
        {stats.map(([label, value]) => (
          <article className="stat" key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </article>
        ))}
      </section>

      <section className="search-card">
        <h2>Find client</h2>
        <div className="search">
          <input placeholder="Email or Click ID" />
          <button>Search</button>
        </div>
      </section>

      <section className="card">
        <div className="card-title">
          <div>
            <p className="eyebrow">RECENT CONVERSION</p>
            <h2>gabrielrcorrea@hotmail.com</h2>
          </div>
          <span className="badge">FTD</span>
        </div>
        <div className="timeline">
          <div><b>REG</b><span>13.08.2026</span></div>
          <div><b>FTD</b><span>16.08.2026 · $247.84</span></div>
          <div><b>FT</b><span>17.08.2026</span></div>
        </div>
      </section>

      <nav>
        <button className="active">Dashboard</button>
        <button>Clients</button>
        <button>Stats</button>
        <button>More</button>
      </nav>
    </main>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode><App /></React.StrictMode>
);
