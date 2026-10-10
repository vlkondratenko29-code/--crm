import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api.js";

// Buying view: Ads Manager spend joined with leads -> dialogs -> REG -> FTD.
const LEVELS = [["campaign", "Кампании"], ["adset", "Группы"], ["ad", "Креативы"]];

const cur = (v, c) => v == null ? "—" : (c === "USD" ? "$" : "") + Number(v).toLocaleString("ru-RU", { maximumFractionDigits: v < 10 ? 2 : 0 }) + (c && c !== "USD" ? " " + c : "");
const pct = v => v == null ? "—" : v + "%";

function Cell({ label, value, tone }) {
  return <span className={tone || ""}>{value}<small>{label}</small></span>;
}

export default function Buying({ username, days }) {
  const [level, setLevel] = useState("campaign");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    setData(null);
    try { setData(await api(`/api/v1/stats/buying?days=${days}&level=${level}`, { username })); setError(""); }
    catch (e) { setError(e.message); }
  }, [days, level, username]);
  useEffect(() => { load(); }, [load]);

  async function upload(e) {
    const files = [...(e.target.files || [])];
    if (!files.length) return;
    setBusy(true); setMsg("");
    try {
      const form = new FormData();
      files.forEach(f => form.append("files", f));
      const r = await api("/api/v1/ads/spend/import", { username, method: "POST", body: form, timeout: 60000 });
      setMsg(`Загружено: ${r.rows} строк, ${r.days?.[0] || ""} — ${r.days?.[1] || ""}, расход ${r.spend} ${(r.currency || []).join(", ")}`);
      await load();
    } catch (err) { setMsg(err.message); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }

  const c = data?.currency;
  const t = data?.total;
  return (
    <div className="buying">
      <div className="buy-upload">
        <div>
          <b>Расход из Ads Manager</b>
          <small>{data?.last_upload ? "Последняя загрузка: " + new Date(data.last_upload + "Z").toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "Ещё не загружали"}</small>
        </div>
        <label className={"upload-btn" + (busy ? " disabled" : "")}>
          {busy ? "Загружаю…" : "Загрузить CSV"}
          <input ref={fileRef} type="file" accept=".csv,text/csv" multiple hidden disabled={busy} onChange={upload} />
        </label>
      </div>
      {msg && <div className="team-message">{msg}</div>}
      <p className="recon-hint">Ads Manager → Отчёты → разбивка «По дням» → уровень кампаний, групп или объявлений → Экспорт CSV. Можно грузить каждый день: повторные дни перезаписываются.</p>

      {error && <div className="team-message">{error}</div>}
      {t && <div className="buy-kpis">
        <div><span>Расход</span><b className="t-money">{cur(t.spend, c)}</b></div>
        <div><span>Цена лида</span><b>{cur(t.cpl, c)}</b></div>
        <div><span>Ответили боту</span><b>{pct(t.dialog_rate)}</b></div>
        <div><span>Цена реги</span><b className="t-reg">{cur(t.cp_reg, c)}</b></div>
        <div><span>Цена депа</span><b className="t-ftd">{cur(t.cp_ftd, c)}</b></div>
        <div><span>ROI</span><b className={t.roi == null ? "" : t.roi >= 0 ? "t-ftd" : "t-bad"}>{pct(t.roi)}</b></div>
      </div>}

      <div className="st-tabs">{LEVELS.map(([k, l]) => <button key={k} className={level === k ? "selected" : ""} onClick={() => setLevel(k)}>{l}</button>)}</div>
      {!data && !error && <p className="subtitle">Считаю…</p>}
      {data && <div className="st-list">
        {data.rows.map((r, i) => <div className="buy-row" key={i}>
          <div className="buy-head">
            <div>
              <b>{level === "campaign" ? r.campaign : level === "adset" ? (r.adset || "Без группы") : (r.ad || "Без объявления")}</b>
              {level !== "campaign" && <small>{r.campaign}{level === "ad" && r.adset ? " · " + r.adset : ""}</small>}
            </div>
            {r.low_data ? <span className="buy-flag">мало данных</span> : null}
          </div>
          <div className="buy-grid">
            <Cell label="расход" value={cur(r.spend, c)} tone="t-money" />
            <Cell label="лиды" value={r.leads} />
            <Cell label="цена лида" value={cur(r.cpl, c)} />
            <Cell label="ответили" value={pct(r.dialog_rate)} />
            <Cell label="реги" value={r.reg} tone="t-reg" />
            <Cell label="цена реги" value={cur(r.cp_reg, c)} tone="t-reg" />
            <Cell label="депы" value={r.ftd} tone="t-ftd" />
            <Cell label="цена депа" value={cur(r.cp_ftd, c)} tone="t-ftd" />
            <Cell label="сумма депов" value={cur(r.deposits, "USD")} />
            <Cell label="ROI" value={pct(r.roi)} tone={r.roi == null ? "" : r.roi >= 0 ? "t-ftd" : "t-bad"} />
          </div>
        </div>)}
        {!data.rows.length && <div className="empty-state">Нет данных за период. Загрузи расход из Ads Manager.</div>}
        <p className="recon-hint">«Мало данных» — меньше {data.min_leads} лидов: по такой выборке рано отключать или масштабировать. «Ответили боту» — человек ответил на приветствие или дошёл до реги. Реги и депы подтягиваются из отчёта FxPro — загружай его в «Сверку» каждый день.</p>
      </div>}
    </div>
  );
}
