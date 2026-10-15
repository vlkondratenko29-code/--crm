import React, { useEffect, useRef, useState } from "react";
import { api, saveSession } from "./api.js";

// CRM website (outside Telegram): sign in with the Telegram Login Widget.
// Only people added in «Команда» get in; the server checks Telegram's signature.
export default function Login({ notice }) {
  const box = useRef(null);
  const [config, setConfig] = useState(null);
  const [msg, setMsg] = useState(notice || "");

  useEffect(() => {
    api("/api/v1/auth/config").then(setConfig).catch(e => setMsg(e.message));
  }, []);

  useEffect(() => {
    if (!config?.enabled || !box.current) return;
    window.onTelegramAuth = async (tgUser) => {
      setMsg("Проверяю…");
      try {
        const r = await api("/api/v1/auth/telegram-login", { method: "POST", body: tgUser });
        saveSession({ token: r.token, username: r.username, expires: r.expires });
        window.location.reload();
      } catch (e) { setMsg(e.status === 403 ? `${e.message}. Попроси админа добавить тебя во вкладке «Команда».` : e.message); }
    };
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.async = true;
    script.setAttribute("data-telegram-login", config.bot_username);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-radius", "12");
    script.setAttribute("data-onauth", "onTelegramAuth(user)");
    box.current.innerHTML = "";
    box.current.appendChild(script);
  }, [config]);

  return (
    <main className="app login-page">
      <header><div><p className="eyebrow">BROKER CRM</p><h1>Вход</h1></div></header>
      <section className="card login-card">
        <h2>Войди через Telegram</h2>
        <p className="subtitle">Тот же аккаунт, что в мини-аппе. Войти могут только участники, добавленные во вкладке «Команда».</p>
        <div className="login-widget" ref={box} />
        {config && !config.enabled && <p className="subtitle">Вход на сайте ещё не настроен на сервере.</p>}
        {msg && <div className="team-message">{msg}</div>}
      </section>
    </main>
  );
}
