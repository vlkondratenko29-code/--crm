import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";

const MATCH_LABELS = { uid: "UID", label: "Label / Click ID", email: "Email", phone: "Phone" };
const FIELD_LABELS = {
  account_id: "Account", email: "Email", phone: "Phone", label: "Label", name: "Name", country: "Country",
  registration_date: "Reg date", ftd_date: "FTD date", ftd_amount: "FTD amount", deposits: "Deposits",
  withdrawals: "Withdrawals", balance: "Balance", last_trade_date: "Last trade",
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
      {rows.length > 5 && <button className="load-more" onClick={() => setOpen(o => !o)}>{open ? "Show less" : `Show all ${rows.length}`}</button>}
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
      <div><b>{x.email || x.name || x.lead_key}</b><small>{[x.campaign, x.source].filter(Boolean).join(" · ") || "No attribution"}</small></div>
      <span>{extra(x)}</span>
    </div>
  );

  return (
    <section className="role-dashboard recon-dashboard">
      <div className="role-hero"><div><p className="eyebrow">RECONCILIATION</p><h2>Chatterfy vs brokers</h2><p>Who Chatterfy says deposited, and what the broker reports show.</p></div><span>⚖️</span></div>

      <div className="buying-filters recon-filters">
        <select value={broker} onChange={e => setBroker(e.target.value)}>
          <option value="">All brokers</option>
          {(data?.brokers || []).map(b => <option key={b} value={b}>{b}</option>)}
        </select>
        <button className="load-more" onClick={load} disabled={loading}>{loading ? "Checking…" : "Refresh"}</button>
      </div>
      {error && <div className="team-message">{error}</div>}

      {data && <>
        <div className="exec-kpis">
          <div><span>MATCHED LEADS</span><b>{c.matched} / {c.leads}</b></div>
          <div><span>BY UID</span><b>{c.by_uid ?? 0}</b></div>
          <div><span>BY LABEL</span><b>{c.by_label}</b></div>
          <div><span>BY EMAIL</span><b>{c.by_email}</b></div>
          <div><span>BY PHONE</span><b>{c.by_phone}</b></div>
          <div><span>BROKER CLIENTS</span><b>{c.broker_clients}</b></div>
          <div><span>LABEL FILLED</span><b>{c.label_coverage}%</b></div>
        </div>
        {c.label_coverage < 50 && <p className="recon-hint warn">Most broker rows have an empty Label. Add the Chatterfy Click ID to the affiliate link so the broker report carries it. Until then matching falls back to email and phone.</p>}

        <Section title="Chatterfy FTD, broker shows no deposit" rows={data.ftd_no_deposit}
          hint="Chatterfy recorded a deposit, but the broker report has no matching client or no deposit."
          empty="Nothing to check."
          render={leadRow(x => (x.reason === "not_found" ? "not found" : "no deposit") + (x.chatterfy_ftd != null ? " · " + money(x.chatterfy_ftd) : ""))} />

        <Section title="Broker deposit, no FTD in Chatterfy" rows={data.deposit_no_ftd}
          hint="The broker has money from this lead, but Chatterfy never sent an FTD. The postback may be missing."
          empty="Nothing to check."
          render={leadRow(x => `${x.stage} · broker ${money(x.broker_deposits)}`)} />

        <Section title="FTD amount differs" rows={data.amount_mismatch}
          empty="All FTD amounts agree."
          render={leadRow(x => `Chatterfy ${money(x.chatterfy_ftd)} · broker ${money(x.broker_ftd ?? x.broker_deposits)}`)} />

        <Section title="Registered in Chatterfy, not found at broker" rows={data.reg_not_found}
          empty="Every registered lead was found."
          render={leadRow(x => "REG")} />

        <Section title="Broker clients without a Chatterfy lead" rows={data.unattributed}
          hint="Clients in broker reports that could not be linked to any Chatterfy lead. Sorted by deposits."
          empty="Every broker client is linked."
          render={(x, i) => (
            <div key={x.broker + x.account_id + i}>
              <div><b>{x.email || x.name || x.account_id}</b><small>{x.broker} · #{x.account_id}{x.country ? " · " + x.country : ""}{x.accounts > 1 ? ` · ${x.accounts} accounts` : ""}</small></div>
              <span>{money(x.deposits)}</span>
            </div>
          )} />
      </>}

      {isAdmin && <ChatterfyUpload username={username} onDone={load} />}
      {isAdmin && <BrokerUpload username={username} brokers={data?.brokers || []} onDone={load} />}
    </section>
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
      setMsg(`New leads: ${r.created} · updated: ${r.updated} · REG/FTD from tags: ${r.events_from_tags}`);
      onDone?.();
    } catch (err) { setMsg(err.message); }
    finally { setBusy(false); }
  }

  return (
    <div className="recon-upload">
      <div className="section-title">Upload Chatterfy users</div>
      <p className="recon-hint">Chatterfy → bot → Users → Export CSV. Adds every chat to the CRM, including people who only wrote in DM, with their tags. Upload one file per bot.</p>
      <label className={"upload-btn" + (busy ? " busy" : "")}>
        {busy ? "Uploading…" : "Choose chats.csv"}
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
    if (!broker.trim()) { setError("Type the broker name first, e.g. FxPro"); return; }
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
      <div className="section-title">Upload broker report</div>
      <p className="recon-hint">CSV export from any broker's partner cabinet. Columns are recognised automatically (email, phone, label / sub ID, deposits, dates). For FxPro use the "Clients" report.</p>
      <div className="note-form">
        <input list="broker-names" value={broker} onChange={e => setBroker(e.target.value)} placeholder="Broker name, e.g. FxPro" />
        <datalist id="broker-names">{brokers.map(b => <option key={b} value={b} />)}</datalist>
        <label className={"upload-btn" + (busy ? " busy" : "")}>
          {busy ? "Uploading…" : "Choose CSV"}
          <input type="file" accept=".csv,text/csv" multiple disabled={busy} onChange={upload} />
        </label>
      </div>
      {error && <div className="team-message">{error}</div>}
      {result && <div className="team-message">
        <b>{result.broker}: {result.accounts} rows imported.</b> With label {result.with_label}, email {result.with_email}, phone {result.with_phone}, deposit {result.with_deposit}.
        {result.missing_keys?.length > 0 && <> No column found for: {result.missing_keys.join(", ")}.</>}
        {(result.columns || []).map(f => <div key={f.file} className="col-map">{f.file}: {Object.entries(f.columns).map(([k, v]) => `${FIELD_LABELS[k] || k} ← "${v}"`).join(" · ") || "no known columns"}</div>)}
      </div>}
    </div>
  );
}
