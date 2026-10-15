// Shared API helper. Every request carries the signed Telegram initData so the
// backend can verify who is calling. The username header is only a fallback
// for local development (the backend ignores it once TELEGRAM_BOT_TOKEN is set).
const tg = window.Telegram?.WebApp;
const BASE = import.meta.env.VITE_API_URL || "";

// Outside Telegram (the CRM website) the user signs in with the Telegram Login Widget and
// gets a signed session token, kept in this browser.
const SESSION_KEY = "crm_session";

export function getSession() {
  try {
    const s = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    return s && s.token && s.expires * 1000 > Date.now() ? s : null;
  } catch { return null; }
}
export function saveSession(session) {
  try { localStorage.setItem(SESSION_KEY, JSON.stringify(session)); } catch { /* private mode: session lasts this page only */ }
}
export function clearSession() {
  try { localStorage.removeItem(SESSION_KEY); } catch { /* nothing to clear */ }
}

export function authHeaders(username) {
  const headers = { "X-Telegram-Init-Data": tg?.initData || "" };
  if (username) headers["X-Telegram-Username"] = username;
  const session = !tg?.initData && getSession();
  if (session) headers["Authorization"] = "Bearer " + session.token;
  return headers;
}

export async function api(path, { username, method = "GET", body, headers = {}, timeout = 30000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const isForm = body instanceof FormData;
  try {
    const res = await fetch(BASE + path, {
      method,
      signal: controller.signal,
      headers: {
        ...authHeaders(username),
        ...(body && !isForm ? { "Content-Type": "application/json" } : {}),
        ...headers,
      },
      body: body ? (isForm ? body : JSON.stringify(body)) : undefined,
    });
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 300) }; }
    if (!res.ok) {
      const error = new Error(data.detail || `HTTP ${res.status}`);
      error.status = res.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error(`CRM did not respond in ${Math.round(timeout / 1000)} seconds. Check that Render and Supabase are up.`);
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
