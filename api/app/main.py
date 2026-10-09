from datetime import datetime, timedelta, timezone
import csv
import json
import os
import io
import threading
import uuid
import re

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import validate_init_data
from . import brokers as broker_lib

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("DB_PATH", "broker_crm.db")
CHATTERFY_WEBHOOK_URL = os.getenv("CHATTERFY_WEBHOOK_URL", "")
# Optional shared secret for /webhook/chatterfy. When set, Chatterfy must call
# the webhook with ?secret=<value> (or the X-Webhook-Secret header).
CHATTERFY_WEBHOOK_SECRET = os.getenv("CHATTERFY_WEBHOOK_SECRET", "")
# Bot token from @BotFather. Enables signed Telegram initData authentication.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Chat (user or group) that receives new FTD/REG notifications.
TELEGRAM_NOTIFY_CHAT_ID = os.getenv("TELEGRAM_NOTIFY_CHAT_ID", "")
NOTIFY_EVENTS = {x.strip().upper() for x in os.getenv("NOTIFY_EVENTS", "FTD").split(",") if x.strip()}
# Legacy username-header auth is only allowed while no bot token is configured,
# unless explicitly enabled (e.g. for local development).
ALLOW_USERNAME_HEADER = os.getenv("ALLOW_USERNAME_HEADER", "0" if TELEGRAM_BOT_TOKEN else "1") == "1"

PRIMARY_ADMINS = ("jokwq", "nodari777")
ROLES = {"admin", "head_buying", "seo", "handler"}
WORK_STATUSES = ("new", "in_progress", "callback", "no_answer", "won", "lost")
STAGE_ORDER = {"LEAD": 0, "REG": 1, "FTD": 2, "FT": 3}


class _SqliteConn:
    """Small adapter so the same `%s` SQL runs on SQLite for local development."""

    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _sql(sql):
        return sql.replace("%s", "?")

    def execute(self, sql, params=()):
        return self._conn.execute(self._sql(sql), params)

    def cursor(self):
        adapter = self

        class _Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def executemany(self, sql, rows):
                return adapter._conn.executemany(adapter._sql(sql), rows)

            def execute(self, sql, params=()):
                return adapter.execute(sql, params)

        return _Cursor()

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def db():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("psycopg is required when DATABASE_URL is configured")
        return psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            connect_timeout=20,
            prepare_threshold=None,
        )

    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return _SqliteConn(conn)


def add_column(conn, table, column, col_type):
    if DATABASE_URL:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}")
        return
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


app = FastAPI(title="Broker CRM API", version="0.6.0")

def init_db():
    conn = db()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS broker_clients (
                email TEXT PRIMARY KEY,
                broker_id TEXT,
                status TEXT,
                country TEXT,
                click_id TEXT,
                registration_date TEXT,
                first_fund_date TEXT,
                first_fund_amount DOUBLE PRECISION,
                first_trade_date TEXT,
                last_trade_date TEXT,
                net_deposits DOUBLE PRECISION,
                deposits DOUBLE PRECISION,
                latest_balance DOUBLE PRECISION,
                trading_volume DOUBLE PRECISION
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fxpro_accounts (
                login TEXT PRIMARY KEY,
                email TEXT,
                name TEXT,
                country TEXT,
                jurisdiction TEXT,
                ib_group TEXT,
                registration_date TEXT,
                active TEXT,
                currency TEXT,
                usd DOUBLE PRECISION,
                deposits DOUBLE PRECISION,
                withdrawals DOUBLE PRECISION,
                latest_balance DOUBLE PRECISION,
                last_trade_date TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chatterfy_leads (
                chat_id TEXT PRIMARY KEY,
                email TEXT,
                click_id TEXT,
                last_synced_event TEXT,
                attribution_json TEXT
            )
        """)
        add_column(conn, "chatterfy_leads", "first_seen_at", "TEXT")
        add_column(conn, "chatterfy_leads", "updated_at", "TEXT")
        add_column(conn, "chatterfy_leads", "phone", "TEXT")
        add_column(conn, "chatterfy_leads", "uid", "TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crm_users (
                username TEXT PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'handler',
                active BOOLEAN NOT NULL DEFAULT TRUE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS crm_events (
                event_key TEXT PRIMARY KEY,
                email TEXT,
                event_type TEXT NOT NULL,
                event_date TEXT,
                amount DOUBLE PRECISION,
                source TEXT NOT NULL,
                broker_id TEXT,
                fxpro_login TEXT,
                chat_id TEXT,
                metadata_json TEXT,
                created_at TEXT
            )
        """)
        # Handler workflow: who owns a lead and where it is in processing.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_work (
                lead_key TEXT PRIMARY KEY,
                assignee TEXT,
                work_status TEXT NOT NULL DEFAULT 'new',
                updated_at TEXT,
                updated_by TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_notes (
                id TEXT PRIMARY KEY,
                lead_key TEXT NOT NULL,
                author TEXT,
                body TEXT NOT NULL,
                created_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_notes_key ON lead_notes(lead_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_crm_events_email ON crm_events(email)")
        add_column(conn, "fxpro_accounts", "label", "TEXT")
        add_column(conn, "fxpro_accounts", "phone", "TEXT")
        # Broker-agnostic account store used for reconciliation with Chatterfy.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS broker_accounts (
                broker TEXT NOT NULL,
                account_id TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                label TEXT,
                name TEXT,
                country TEXT,
                registration_date TEXT,
                ftd_date TEXT,
                ftd_amount DOUBLE PRECISION,
                deposits DOUBLE PRECISION,
                withdrawals DOUBLE PRECISION,
                balance DOUBLE PRECISION,
                last_trade_date TEXT,
                imported_at TEXT,
                PRIMARY KEY (broker, account_id)
            )
        """)
        # Existing FxPro account rows become FxPro broker accounts.
        conn.execute("""
            INSERT INTO broker_accounts
            (broker, account_id, email, phone, label, name, country, registration_date,
             deposits, withdrawals, balance, last_trade_date, imported_at)
            SELECT 'FxPro', login, lower(trim(email)), phone, label, name, country, registration_date,
                   deposits, withdrawals, latest_balance, last_trade_date, NULL
            FROM fxpro_accounts WHERE TRUE
            ON CONFLICT(broker, account_id) DO NOTHING
        """)
        # Simple key/value settings editable from the Mini App (e.g. Chatterfy postback URL).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT,
                updated_by TEXT
            )
        """)
        # Broker events (REG/FTD) already pushed to Chatterfy, so each is sent once.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chatterfy_pushes (
                lead_key TEXT NOT NULL,
                event TEXT NOT NULL,
                click_id TEXT,
                amount DOUBLE PRECISION,
                status TEXT,
                response TEXT,
                sent_at TEXT,
                PRIMARY KEY (lead_key, event)
            )
        """)
        # Personal Telegram notifications: user chat id, callback reminders, stage notices.
        add_column(conn, "crm_users", "tg_id", "TEXT")
        add_column(conn, "lead_work", "callback_at", "TEXT")
        add_column(conn, "lead_work", "callback_notified_at", "TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS handler_notices (
                lead_key TEXT NOT NULL,
                event TEXT NOT NULL,
                assignee TEXT,
                sent_at TEXT,
                PRIMARY KEY (lead_key, event)
            )
        """)
        for admin_username in PRIMARY_ADMINS:
            conn.execute(
                "INSERT INTO crm_users(username, role, active) VALUES(%s,%s,TRUE) ON CONFLICT(username) DO UPDATE SET role='admin', active=TRUE",
                (admin_username, "admin"),
            )
        # Remove legacy synthetic FxPro events created by older CRM versions.
        # Chatterfy is the only authoritative source for REG/FTD/FT.
        conn.execute("DELETE FROM crm_events WHERE source = 'fxpro'")
        conn.commit()
    finally:
        conn.close()

init_db()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def disable_web_cache(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


class UserMe(BaseModel):
    username: str
    role: str
    is_admin: bool
    notify_ready: bool = False


def parse_date(value: str | None):
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f0", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return value


def to_float(value: str | None):
    if not value:
        return None
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def parse_fxpro_report(raw: bytes):
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:10000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)

    def value(row, *names):
        normalized = {str(k).strip().lower().replace("\ufeff", ""): v for k, v in row.items() if k is not None}
        for name in names:
            v = normalized.get(name.lower())
            if v not in (None, ""):
                return v
        return None

    clients = []
    for row in rows:
        email = (value(row, "EmailAddress", "Email Address", "Email", "email") or "").strip().lower()
        if not email or "@" not in email:
            continue

        registration = parse_date(value(row, "RegistrationDate", "Registration Date", "DateReg"))
        first_fund = parse_date(value(row, "First Fund Date", "FirstFundDate", "FTD Date"))
        first_trade = parse_date(value(row, "First Trade Date", "FirstTradeDate", "FT Date"))

        events = []
        if registration:
            events.append({"type": "REG", "date": registration})
        if first_fund:
            events.append({"type": "FTD", "date": first_fund, "amount": to_float(value(row, "First External Fund USD", "First Fund USD", "First Fund Amount"))})
        if first_trade:
            events.append({"type": "FT", "date": first_trade})

        clients.append({
            "email": email,
            "broker_id": value(row, "ProfileGUID", "Profile GUID", "Broker ID", "BrokerId"),
            "status": value(row, "Status"),
            "country": value(row, "Residential Country", "Country"),
            "click_id": value(row, "ClickID", "Click ID", "ClickId") or None,
            "registration_date": registration,
            "first_fund_date": first_fund,
            "first_fund_amount": to_float(value(row, "First External Fund USD", "First Fund USD", "First Fund Amount")),
            "first_trade_date": first_trade,
            "last_trade_date": parse_date(value(row, "Last Trade Date", "LastTradeDate")),
            "net_deposits": to_float(value(row, "NetDeposits USD (External)", "Net Deposits USD", "Net Deposits")),
            "deposits": to_float(value(row, "Deposits USD (External)", "Deposits USD", "Deposits")),
            "latest_balance": to_float(value(row, "Latest Balance USD", "Latest Balance", "Balance USD")),
            "trading_volume": to_float(value(row, "Trading Volume USD", "Trading Volume")),
            "events": events,
        })

    return clients


def parse_fxpro_clients_report(raw: bytes):
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:10000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = list(reader)

    def value(row, *names):
        normalized = {}
        for k, v in row.items():
            if k is None:
                continue
            key = str(k).strip().lower().replace("\ufeff", "").replace('"', "")
            normalized[key] = v
            normalized[key.replace(" ", "").replace("_", "").replace("-", "")] = v
        for name in names:
            clean_name = str(name).lower()
            v = normalized.get(clean_name)
            if v in (None, ""):
                v = normalized.get(clean_name.replace(" ", "").replace("_", "").replace("-", ""))
            if v not in (None, ""):
                return v
        return None

    def email_value(row):
        found = value(row, "Email", "Email Address", "EmailAddress", "E-mail", "E Mail")
        if found:
            return found
        # Fallback only for the email field: some exports rename the column.
        for v in row.values():
            text = str(v or "").strip()
            if "@" in text and "." in text.split("@")[-1]:
                return text
        return None

    accounts = []
    for row in rows:
        login = str(value(row, "Логин", "Login", "Account", "Account ID") or "").strip()
        if not login:
            continue
        if "@" in login:
            continue  # not an account report row
        email = (email_value(row) or "").strip().lower()
        if email and "@" not in email:
            email = ""
        deposits = to_float(value(row, "Депозиты", "Deposits"))
        withdrawals = to_float(value(row, "Выводы", "Withdrawals"))
        # FxPro exports withdrawals as signed negative amounts in some reports.
        # Store them as positive outflows so Finance can consistently calculate
        # Net Deposits = Deposits - Withdrawals.
        if withdrawals is not None and withdrawals < 0:
            withdrawals = abs(withdrawals)
        accounts.append({
            "login": login,
            "email": email or None,
            "name": value(row, "Имя", "Name"),
            "country": value(row, "Страна", "Country"),
            "jurisdiction": value(row, "Юрисдикция", "Jurisdiction"),
            "ib_group": value(row, "IB группа", "IB Group"),
            "registration_date": parse_date(value(row, "Дата регистрации", "Registration Date", "RegistrationDate")),
            "active": value(row, "Активен", "Active"),
            "currency": value(row, "Валюта", "Currency"),
            "usd": to_float(value(row, "USD")),
            "deposits": deposits,
            "withdrawals": withdrawals,
            "latest_balance": to_float(value(row, "Баланс в реальном времени", "Real-time Balance", "Latest Balance")),
            "label": broker_lib.normalize_label(value(row, "Лейбл", "Label")),
            "phone": broker_lib.normalize_phone(value(row, "Мобильный телефон", "Mobile Phone", "Phone", "Телефон")),
            "last_trade_date": parse_date(value(row, "Последняя сделка", "Last Trade", "Last Trade Date")),
        })
    return accounts

def record_event(conn, *, email, event_type, event_date=None, amount=None, source="unknown",
                 broker_id=None, fxpro_login=None, chat_id=None, metadata=None):
    """Persist a deterministic client event without creating duplicates."""
    if not email or not event_type:
        return False
    import hashlib
    email_key = email.strip().lower()
    raw_key = "|".join([
        email_key, str(event_type).upper(), str(event_date or ""),
        str(source or ""), str(fxpro_login or ""), str(broker_id or ""),
        str(chat_id or "")
    ])
    event_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    now = datetime.utcnow().isoformat()
    existed = conn.execute("SELECT 1 FROM crm_events WHERE event_key=%s", (event_key,)).fetchone() is not None
    conn.execute("""
        INSERT INTO crm_events
        (event_key,email,event_type,event_date,amount,source,broker_id,fxpro_login,chat_id,metadata_json,created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(event_key) DO UPDATE SET
            amount=excluded.amount,
            metadata_json=excluded.metadata_json
    """, (
        event_key, email_key, str(event_type).upper(), event_date, amount,
        source, broker_id, fxpro_login, chat_id, metadata_json, now
    ))
    return not existed

def record_fxpro_client_events(conn, client):
    for event in client.get("events", []):
        record_event(
            conn,
            email=client.get("email"),
            event_type=event.get("type"),
            event_date=event.get("date"),
            amount=event.get("amount"),
            source="fxpro",
            broker_id=client.get("broker_id"),
            metadata={"status": client.get("status"), "click_id": client.get("click_id")}
        )

def record_fxpro_account_events(conn, account):
    email = account.get("email")
    if not email:
        return
    if account.get("registration_date"):
        record_event(
            conn, email=email, event_type="REG",
            event_date=account.get("registration_date"),
            source="fxpro", fxpro_login=account.get("login"),
            metadata={"account_report": True}
        )
    if account.get("last_trade_date"):
        # The full account report exposes last trade, not the first trade date.
        # Do not label it FT; that would fabricate an event.
        pass

def find_client(email: str | None, click_id: str | None = None):
    conn = db()
    row = None
    if email:
        row = conn.execute("SELECT * FROM broker_clients WHERE lower(email)=%s", (email.strip().lower(),)).fetchone()
    if row is None and click_id:
        row = conn.execute("SELECT * FROM broker_clients WHERE lower(coalesce(click_id,''))=%s", (click_id.strip().lower(),)).fetchone()
    conn.close()
    return dict(row) if row else None


def sync_client_to_chatterfy(chat_id: str, client: dict):
    if not CHATTERFY_WEBHOOK_URL:
        return {"sent": False, "reason": "CHATTERFY_WEBHOOK_URL is not configured"}

    import urllib.parse
    import urllib.request

    params = {
        "chat_id": chat_id,
        "fields.broker_id": client.get("broker_id") or "",
        "fields.broker_event": "FT" if client.get("first_trade_date") else ("FTD" if client.get("first_fund_date") else "REG"),
        "fields.datereg": client.get("registration_date") or "",
        "fields.deposit_amount": client.get("first_fund_amount") if client.get("first_fund_amount") is not None else "",
    }
    tags = []
    if client.get("registration_date"): tags.append("REG")
    if client.get("first_fund_date"): tags.append("FTD")
    if client.get("first_trade_date"): tags.append("FT")
    if tags:
        params["assign_tags"] = ",".join(tags)
    url = CHATTERFY_WEBHOOK_URL + ("&" if "?" in CHATTERFY_WEBHOOK_URL else "?") + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=10) as response:
        return {"sent": True, "status_code": response.status}


def require_access(username: str, conn=None):
    username = username.lstrip("@").strip().lower()
    owns_conn = conn is None
    if owns_conn:
        conn = db()
    row = conn.execute("SELECT username, role, active FROM crm_users WHERE lower(username)=%s", (username,)).fetchone()
    if owns_conn:
        conn.close()
    if not row or not row["active"]:
        raise HTTPException(status_code=403, detail="CRM access is not granted")
    return dict(row)


def require_admin(username: str, conn=None):
    user = require_access(username, conn)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user["username"]


def current_username(
    x_telegram_init_data: str = Header(default=""),
    x_telegram_username: str = Header(default=""),
):
    """Resolve the caller from signed Telegram initData.

    The plain X-Telegram-Username header can be forged by anyone, so it is
    only honoured while TELEGRAM_BOT_TOKEN is not configured (or when
    ALLOW_USERNAME_HEADER=1 is set explicitly for local development).
    """
    if x_telegram_init_data and TELEGRAM_BOT_TOKEN:
        tg_user = validate_init_data(x_telegram_init_data, TELEGRAM_BOT_TOKEN)
        if not tg_user:
            raise HTTPException(status_code=401, detail="Telegram session is invalid or expired. Reopen the Mini App.")
        username = (tg_user.get("username") or "").strip().lower()
        if not username:
            raise HTTPException(status_code=403, detail="Set a Telegram username to use the CRM")
        remember_tg_id(username, tg_user.get("id"))
        return username
    if ALLOW_USERNAME_HEADER:
        return x_telegram_username.lstrip("@").strip().lower()
    raise HTTPException(status_code=401, detail="Open the CRM from Telegram")


class TeamUser(BaseModel):
    username: str
    role: str
    active: bool = True


@app.get("/api/v1/users")
def list_users(x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        require_admin(x_telegram_username, conn)
        rows = conn.execute("SELECT username, role, active FROM crm_users ORDER BY username").fetchall()
        return {"users": [dict(row) for row in rows]}
    finally:
        conn.close()


@app.post("/api/v1/users")
def upsert_user(payload: TeamUser, x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        require_admin(x_telegram_username, conn)
        username = payload.username.lstrip("@").strip().lower()
        if not username:
            raise HTTPException(status_code=400, detail="Username is required")
        if payload.role not in {"admin", "head_buying", "seo", "handler"}:
            raise HTTPException(status_code=400, detail="Invalid role")
        conn.execute(
            "INSERT INTO crm_users(username, role, active) VALUES(%s,%s,%s) ON CONFLICT(username) DO UPDATE SET role=excluded.role, active=excluded.active",
            (username, payload.role, payload.active),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@app.delete("/api/v1/users/{username}")
def deactivate_user(username: str, x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        require_admin(x_telegram_username, conn)
        target = username.lstrip("@").strip().lower()
        if target in {"jokwq", "nodari777"}:
            raise HTTPException(status_code=400, detail="Primary admins cannot be deactivated")
        conn.execute("UPDATE crm_users SET active=FALSE WHERE lower(username)=%s", (target,))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@app.get("/api/v1/me", response_model=UserMe)
def me(x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        user = require_access(x_telegram_username, conn)
        row = conn.execute("SELECT tg_id FROM crm_users WHERE lower(username)=%s", (user["username"].lower(),)).fetchone()
    finally:
        conn.close()
    return {"username": user["username"], "role": user["role"], "is_admin": user["role"] == "admin",
            "notify_ready": bool(row and row["tg_id"] and TELEGRAM_BOT_TOKEN)}


@app.get("/health")
def health():
    return {"status": "ok", "service": "broker-crm-api"}


def clean_attribution_value(value):
    if value is None:
        return None
    value = str(value).strip()
    # Unfilled Chatterfy placeholders arrive literally, e.g. "{datereg}" or "{{x}}".
    if not value or "{{" in value or "}}" in value or (value.startswith("{") and value.endswith("}")):
        return None
    return value

def attribution_text(attr, *keys):
    for key in keys:
        value = clean_attribution_value(attr.get(key))
        if value:
            return value
    return None


def send_telegram_message(chat_id: str, text: str):
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", data=data, timeout=10).read()
    except Exception as exc:  # never break the webhook because of a notification
        print(f"Telegram notify failed: {type(exc).__name__}: {exc}")


def notify_event(event_type: str, email: str | None, amount: float | None, attr: dict):
    if not TELEGRAM_NOTIFY_CHAT_ID or not TELEGRAM_BOT_TOKEN:
        return
    icon = {"FTD": "💰", "REG": "📝", "FT": "📈"}.get(event_type, "🔔")
    lines = [f"{icon} New {event_type}" + (f" · ${amount:,.2f}" if amount else "")]
    if email:
        lines.append(email)
    campaign = attribution_text(attr, "tracker_campaign_name", "tracker_campaign", "campaign_name", "utm_campaign")
    source = attribution_text(attr, "tracker_source_name", "tracker_source", "utm_source")
    if campaign or source:
        lines.append(" · ".join(x for x in (campaign, source) if x))
    threading.Thread(target=send_telegram_message, args=(TELEGRAM_NOTIFY_CHAT_ID, "\n".join(lines)), daemon=True).start()


# ---------------------------------------------------------------------------
# Personal notifications to CRM users in Telegram
# ---------------------------------------------------------------------------

_known_tg_ids = {}


def remember_tg_id(username, tg_id):
    """Store the Telegram user id of whoever opens the Mini App, so the bot can message them."""
    if not tg_id:
        return
    tg_id = str(tg_id)
    if _known_tg_ids.get(username) == tg_id:
        return
    try:
        conn = db()
        try:
            conn.execute("UPDATE crm_users SET tg_id=%s WHERE lower(username)=%s", (tg_id, username))
            conn.commit()
        finally:
            conn.close()
        _known_tg_ids[username] = tg_id
    except Exception as exc:
        print(f"remember_tg_id failed: {type(exc).__name__}: {exc}")


def send_telegram_checked(chat_id, text):
    """Send a message and return (ok, error) so callers can report delivery problems."""
    if not TELEGRAM_BOT_TOKEN:
        return False, "TELEGRAM_BOT_TOKEN is not set"
    if not chat_id:
        return False, "no chat id"
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", data=data, timeout=10).read()
        return True, None
    except urllib.error.HTTPError as exc:
        try:
            detail = _json.loads(exc.read().decode()).get("description")
        except Exception:
            detail = None
        return False, detail or f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def user_tg_id(conn, username):
    row = conn.execute("SELECT tg_id FROM crm_users WHERE lower(username)=%s AND active=TRUE",
                       ((username or "").lower(),)).fetchone()
    return row["tg_id"] if row else None


def notify_user(conn, username, text):
    """Fire-and-forget personal message; silently skipped if the user never opened the Mini App."""
    chat_id = user_tg_id(conn, username)
    if chat_id and TELEGRAM_BOT_TOKEN:
        threading.Thread(target=send_telegram_message, args=(chat_id, text), daemon=True).start()
        return True
    return False


def lead_label(lead):
    name = lead.get("name") or ""
    user = ("@" + lead["tg_username"]) if lead.get("tg_username") else ""
    return " ".join(x for x in (name, user) if x) or lead.get("email") or lead.get("lead_key")


HANDLER_NOTICES_SEEDED = "handler_notices_seeded"


def run_stage_notices(conn):
    """Tell each handler when their lead registers or deposits (from Chatterfy or the broker report).

    The first run only records what already happened, so nobody gets a flood of old events.
    """
    leads = build_leads(conn, {"role": "admin", "username": "system"})
    done = {(r["lead_key"], r["event"]) for r in conn.execute("SELECT lead_key, event FROM handler_notices").fetchall()}
    seeding = get_setting(conn, HANDLER_NOTICES_SEEDED) != "1"
    now = datetime.utcnow().isoformat()
    sent = 0
    for x in leads:
        accs = x.get("matched_accounts") or []
        rank = STAGE_ORDER.get(x["stage"], 0)
        events = []
        if rank >= STAGE_ORDER["REG"] or accs:
            events.append("REG")
        if rank >= STAGE_ORDER["FTD"] or any(broker_lib.account_has_deposit(a) for a in accs):
            events.append("FTD")
        for ev in events:
            if (x["lead_key"], ev) in done:
                continue
            conn.execute(
                "INSERT INTO handler_notices(lead_key, event, assignee, sent_at) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (x["lead_key"], ev, x.get("assignee"), now),
            )
            done.add((x["lead_key"], ev))
            if seeding or not x.get("assignee"):
                continue
            text = (f"💰 Твой клиент внёс депозит: {lead_label(x)}" if ev == "FTD"
                    else f"📝 Твой клиент зарегистрировался: {lead_label(x)}\nДожми до первого депозита.")
            if notify_user(conn, x["assignee"], text):
                sent += 1
    if seeding:
        set_setting(conn, HANDLER_NOTICES_SEEDED, "1", "system")
    conn.commit()
    return sent


def run_callback_reminders(conn):
    now = datetime.utcnow().isoformat()
    rows = conn.execute(
        "SELECT lead_key, assignee, callback_at FROM lead_work WHERE work_status='callback' AND callback_at IS NOT NULL "
        "AND callback_at <= %s AND (callback_notified_at IS NULL OR callback_notified_at < callback_at)",
        (now,),
    ).fetchall()
    if not rows:
        return 0
    leads = {x["lead_key"]: x for x in build_leads(conn, {"role": "admin", "username": "system"})}
    sent = 0
    for r in rows:
        conn.execute("UPDATE lead_work SET callback_notified_at=%s WHERE lead_key=%s", (now, r["lead_key"]))
        lead = leads.get(r["lead_key"]) or {"lead_key": r["lead_key"]}
        if r["assignee"] and notify_user(conn, r["assignee"], f"⏰ Пора связаться с клиентом: {lead_label(lead)}\nТы ставил «Перезвонить» на это время."):
            sent += 1
    conn.commit()
    return sent


NOTIFY_LOOP_SECONDS = int(os.getenv("NOTIFY_LOOP_SECONDS", "120"))


def _notify_loop():
    import time
    time.sleep(20)
    while True:
        try:
            conn = db()
            try:
                run_callback_reminders(conn)
                run_stage_notices(conn)
            finally:
                conn.close()
        except Exception as exc:
            print(f"notify loop error: {type(exc).__name__}: {exc}")
        time.sleep(NOTIFY_LOOP_SECONDS)


if TELEGRAM_BOT_TOKEN and os.getenv("DISABLE_NOTIFY_LOOP", "0") != "1":
    threading.Thread(target=_notify_loop, daemon=True).start()


@app.post("/api/v1/notify/test")
def notify_test(x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        user = require_access(x_telegram_username, conn)
        chat_id = user_tg_id(conn, user["username"])
    finally:
        conn.close()
    if not chat_id:
        raise HTTPException(status_code=400, detail="Открой мини-апп из Telegram, чтобы CRM узнала твой аккаунт")
    ok, err = send_telegram_checked(chat_id, "✅ Уведомления CRM работают. Сюда будут приходить новые лиды, регистрации, депозиты и напоминания.")
    if not ok:
        hint = "Открой бота и нажми Start (или напиши ему любое сообщение), потом проверь ещё раз." if err and ("chat not found" in err or "blocked" in err or "initiate" in err) else err
        raise HTTPException(status_code=400, detail=f"Не удалось отправить: {hint}")
    return {"status": "ok"}


@app.api_route("/webhook/chatterfy", methods=["GET", "POST"])
async def chatterfy_webhook(
    request: Request,
    email: str | None = None, chat_id: str | None = None, click_id: str | None = None,
    name: str | None = None, username: str | None = None, chatterfy_id: str | None = None,
    step_key: str | None = None, created_at: str | None = None,
    broker_id: str | None = None, broker_event: str | None = None, deposit_amount: str | None = None,
    datereg: str | None = None,
    ad_id: str | None = None, site_source_name: str | None = None, utm_term: str | None = None,
    tracker_campaign_type: str | None = None, utm_id: str | None = None, utm_medium: str | None = None,
    utm_source: str | None = None, utm_campaign: str | None = None, campaign_name: str | None = None,
    ad_campaign_id: str | None = None, adset_id: str | None = None, placement: str | None = None,
    adset_name: str | None = None, utm_content: str | None = None, tracker_provider_type: str | None = None,
    tracker_campaign: str | None = None, tracker_source: str | None = None, tracker_domain_id: str | None = None,
    tracker_landing_id: str | None = None, tracker_source_name: str | None = None,
    tracker_campaign_name: str | None = None, secret: str | None = None, phone: str | None = None
):
    if CHATTERFY_WEBHOOK_SECRET:
        provided = secret or request.headers.get("x-webhook-secret") or ""
        import hmac
        if not hmac.compare_digest(provided, CHATTERFY_WEBHOOK_SECRET):
            raise HTTPException(status_code=403, detail="Invalid webhook secret")
    attribution = {
        "ad_id": ad_id, "site_source_name": site_source_name, "utm_term": utm_term,
        "tracker_campaign_type": tracker_campaign_type, "utm_id": utm_id, "utm_medium": utm_medium,
        "utm_source": utm_source, "utm_campaign": utm_campaign, "campaign_name": campaign_name,
        "ad_campaign_id": ad_campaign_id, "adset_id": adset_id, "placement": placement,
        "adset_name": adset_name, "utm_content": utm_content, "tracker_provider_type": tracker_provider_type,
        "tracker_campaign": tracker_campaign, "tracker_source": tracker_source,
        "tracker_domain_id": tracker_domain_id, "tracker_landing_id": tracker_landing_id,
        "tracker_source_name": tracker_source_name, "tracker_campaign_name": tracker_campaign_name,
        "name": name, "username": username, "chatterfy_id": chatterfy_id,
        "step_key": step_key, "created_at": created_at
    }
    if request.method == "POST":
        try:
            payload = await request.json()
            email = email or payload.get("email")
            chat_id = chat_id or payload.get("chat_id") or payload.get("chatId")
            name = name or payload.get("name")
            username = username or payload.get("username")
            chatterfy_id = chatterfy_id or payload.get("chatterfy_id") or payload.get("id")
            phone = phone or payload.get("phone") or payload.get("phone_number")
            step_key = step_key or payload.get("step_key") or payload.get("stepKey")
            created_at = created_at or payload.get("created_at") or payload.get("createdAt")
            click_id = click_id or payload.get("click_id") or payload.get("clickId")
            broker_id = broker_id or payload.get("broker_id") or payload.get("brokerId")
            broker_event = broker_event or payload.get("broker_event") or payload.get("brokerEvent")
            deposit_amount = deposit_amount or payload.get("deposit_amount") or payload.get("depositAmount")
            datereg = datereg or payload.get("datereg") or payload.get("dateReg")
            for key in attribution:
                attribution[key] = attribution[key] or payload.get(key)
        except Exception:
            pass
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    chat_id = str(chat_id)

    attribution = {k: clean_attribution_value(v) for k, v in attribution.items()}
    incoming_attr = {k: v for k, v in attribution.items() if v is not None}

    # The Chatterfy field "UID / email клиента" may hold either an email or the
    # client's broker account number (UID). Keep them apart.
    raw_contact = (email or "").strip()
    uid = broker_lib.normalize_account_id(raw_contact) or broker_lib.normalize_account_id(broker_id)
    normalized_email = broker_lib.normalize_email(raw_contact)
    now = datetime.utcnow().isoformat()
    event_is_new = False
    conn = db()
    try:
        # Merge attribution instead of overwriting it: later funnel steps
        # (e.g. the FTD postback) often arrive without UTM/tracker fields and
        # must not wipe the attribution captured at the start of the funnel.
        existing = conn.execute("SELECT attribution_json FROM chatterfy_leads WHERE chat_id=%s", (chat_id,)).fetchone()
        merged_attr = {}
        if existing and existing["attribution_json"]:
            try:
                merged_attr = json.loads(existing["attribution_json"]) or {}
            except Exception:
                merged_attr = {}
        merged_attr.update(incoming_attr)
        attribution_json = json.dumps(merged_attr, ensure_ascii=False)
        conn.execute(
            "INSERT INTO chatterfy_leads(chat_id,email,click_id,attribution_json,first_seen_at,updated_at,phone,uid) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "uid=COALESCE(excluded.uid,chatterfy_leads.uid), "
            "phone=COALESCE(excluded.phone,chatterfy_leads.phone), "
            "email=COALESCE(excluded.email,chatterfy_leads.email), "
            "click_id=COALESCE(excluded.click_id,chatterfy_leads.click_id), "
            "attribution_json=excluded.attribution_json, "
            "first_seen_at=COALESCE(chatterfy_leads.first_seen_at,excluded.first_seen_at), "
            "updated_at=excluded.updated_at",
            (chat_id, normalized_email, click_id, attribution_json, now, now, broker_lib.normalize_phone(phone), uid)
        )
        if broker_event:
            # Leads without an email are keyed by their chat so REG/FTD still count.
            event_is_new = record_event(
                conn, email=normalized_email or f"chat:{chat_id}", event_type=broker_event,
                event_date=clean_attribution_value(datereg), amount=to_float(deposit_amount),
                source="chatterfy", broker_id=uid or clean_attribution_value(broker_id),
                chat_id=chat_id, metadata={"click_id": click_id, "uid": uid}
            )
        conn.commit()
    finally:
        conn.close()

    if event_is_new and str(broker_event).upper() in NOTIFY_EVENTS:
        notify_event(str(broker_event).upper(), normalized_email, to_float(deposit_amount), merged_attr)

    return {
        "status": "ok",
        "matched": bool(normalized_email and find_client(normalized_email, click_id)),
        "email": normalized_email,
        "chat_id": chat_id,
        "lead_saved": True,
        "event_saved": bool(broker_event),
        "uid": uid,
    }


BROKER_ACCOUNT_FIELDS = ("email", "phone", "label", "name", "country", "registration_date", "ftd_date",
                         "ftd_amount", "deposits", "withdrawals", "balance", "last_trade_date")


def canonical_broker(name: str) -> str:
    name = " ".join(str(name or "").split())[:60]
    return "FxPro" if name.lower() == "fxpro" else name


def upsert_broker_accounts(conn, broker, accounts):
    """Insert/update broker accounts. Fields missing from a report keep their previous value."""
    if not accounts:
        return
    now = datetime.utcnow().isoformat()
    cols = ("broker", "account_id") + BROKER_ACCOUNT_FIELDS + ("imported_at",)
    updates = ",".join(f"{c}=COALESCE(excluded.{c},broker_accounts.{c})" for c in BROKER_ACCOUNT_FIELDS)
    sql = (f"INSERT INTO broker_accounts ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
           f"ON CONFLICT(broker, account_id) DO UPDATE SET {updates}, imported_at=excluded.imported_at")
    rows = [(broker, a["account_id"]) + tuple(a.get(c) for c in BROKER_ACCOUNT_FIELDS) + (now,) for a in accounts]
    with conn.cursor() as cur:
        cur.executemany(sql, rows)


@app.post("/api/v1/broker/import")
async def import_broker_report(broker: str = Form(...), files: list[UploadFile] = File(...),
                               x_telegram_username: str = Depends(current_username)):
    """Import any broker's CSV export. Columns are detected automatically."""
    broker = canonical_broker(broker)
    if not broker:
        raise HTTPException(status_code=400, detail="Broker name is required")
    conn = db()
    try:
        require_admin(x_telegram_username, conn)
        total = []
        mappings = []
        for file in files:
            raw = await file.read()
            accounts, mapping = broker_lib.parse_broker_report(raw)
            mappings.append({"file": file.filename, "columns": mapping})
            upsert_broker_accounts(conn, broker, accounts)
            total.extend(accounts)
        conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Import failed: {type(exc).__name__}: {exc}")
    finally:
        conn.close()
    found = set().union(*[set(m["columns"]) for m in mappings]) if mappings else set()
    chatterfy_push = push_after_import()
    return {
        "status": "ok",
        "chatterfy_push": chatterfy_push,
        "broker": broker,
        "files": len(files),
        "accounts": len(total),
        "with_label": sum(1 for a in total if a["label"]),
        "with_email": sum(1 for a in total if a["email"]),
        "with_phone": sum(1 for a in total if a["phone"]),
        "with_deposit": sum(1 for a in total if broker_lib.account_has_deposit(a)),
        "columns": mappings,
        "missing_keys": [k for k in ("label", "email", "phone") if k not in found],
    }


def parse_chatterfy_export(raw: bytes):
    """Parse Chatterfy 'Users → Export CSV' (Name;Telegram ID;Username;Tags;Started;...)."""
    headers, rows = broker_lib.read_csv(raw)
    norm = {broker_lib._norm_header(h): h for h in headers}
    col = lambda *names: next((norm[broker_lib._norm_header(n)] for n in names if broker_lib._norm_header(n) in norm), None)
    c_id, c_name, c_user = col("Telegram ID", "chat_id", "chatId"), col("Name"), col("Username")
    c_tags, c_started, c_status, c_step = col("Tags"), col("Started"), col("Status"), col("Step")
    c_last = col("Last User Message")
    if not c_id:
        raise HTTPException(status_code=400, detail="This does not look like a Chatterfy users export (no Telegram ID column)")
    leads = []
    for row in rows:
        chat_id = str(row.get(c_id) or "").strip()
        if not chat_id.isdigit():
            continue
        raw_tags = str(row.get(c_tags) or "").strip()
        tags = [t.strip() for t in re.split(r"[,;\n]+", raw_tags) if t.strip()]

        def stamp(column):
            value = str(row.get(column) or "").strip() if column else ""
            for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return datetime.strptime(value, fmt).isoformat()
                except ValueError:
                    pass
            return None
        leads.append({
            "chat_id": chat_id,
            "name": (row.get(c_name) or "").strip() or None if c_name else None,
            "username": (row.get(c_user) or "").strip().lstrip("@") or None if c_user else None,
            "tags": tags,
            "started": stamp(c_started),
            "last_message": stamp(c_last),
            "status": (row.get(c_status) or "").strip() or None if c_status else None,
            "step": (row.get(c_step) or "").strip() or None if c_step else None,
        })
    return leads


@app.post("/api/v1/chatterfy/import")
async def import_chatterfy_export(files: list[UploadFile] = File(...), x_telegram_username: str = Depends(current_username)):
    """Bring every Chatterfy chat into the CRM, including people who only wrote in DM."""
    conn = db()
    created = updated = events = 0
    try:
        require_admin(x_telegram_username, conn)
        for file in files:
            for lead in parse_chatterfy_export(await file.read()):
                row = conn.execute("SELECT attribution_json FROM chatterfy_leads WHERE chat_id=%s", (lead["chat_id"],)).fetchone()
                attr = {}
                if row and row["attribution_json"]:
                    try:
                        attr = json.loads(row["attribution_json"]) or {}
                    except Exception:
                        attr = {}
                # Webhook data wins; the export only fills gaps and refreshes tags/step.
                for key in ("name", "username"):
                    if lead[key] and not attr.get(key):
                        attr[key] = lead[key]
                attr["tags"] = ", ".join(lead["tags"]) or None
                attr["chatterfy_status"] = lead["status"]
                attr["chatterfy_step"] = lead["step"]
                attr = {k: v for k, v in attr.items() if v is not None}
                now = datetime.utcnow().isoformat()
                conn.execute(
                    "INSERT INTO chatterfy_leads(chat_id, attribution_json, first_seen_at, updated_at) VALUES(%s,%s,%s,%s) "
                    "ON CONFLICT(chat_id) DO UPDATE SET attribution_json=excluded.attribution_json, "
                    "first_seen_at=COALESCE(chatterfy_leads.first_seen_at, excluded.first_seen_at), "
                    "updated_at=COALESCE(excluded.updated_at, chatterfy_leads.updated_at)",
                    (lead["chat_id"], json.dumps(attr, ensure_ascii=False), lead["started"] or now, lead["last_message"]),
                )
                if row:
                    updated += 1
                else:
                    created += 1
                # REG / FTD tags set by operators count as funnel events.
                # Chatterfy may export tags as "CRM: REG", "FTD | ...",
                # JSON-like text, etc. Detect the funnel tokens inside each
                # tag instead of requiring an exact tag string.
                tag_text = " | ".join(str(t) for t in lead["tags"])
                detected = []
                for event_type in ("FTD", "REG", "FT"):
                    if re.search(r"(?<![a-z0-9])" + event_type.casefold() + r"(?![a-z0-9])", tag_text.casefold()):
                        detected.append(event_type)
                for event_type in detected:
                    if record_event(conn, email=f"chat:{lead['chat_id']}", event_type=event_type, source="chatterfy",
                                    chat_id=lead["chat_id"], metadata={"from": "export_tag", "tag": event_type}):
                        events += 1
        conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Chatterfy import failed: {type(exc).__name__}: {exc}")
    finally:
        conn.close()
    return {"status": "ok", "created": created, "updated": updated, "events_from_tags": events}


@app.post("/api/v1/broker/fxpro/import")
async def import_fxpro_report(files: list[UploadFile] = File(...), x_telegram_username: str = Depends(current_username)):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    conn = None
    try:
        conn = db()
        sql = """INSERT INTO broker_clients
        (email,broker_id,status,country,click_id,registration_date,first_fund_date,first_fund_amount,first_trade_date,last_trade_date,net_deposits,deposits,latest_balance,trading_volume)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(email) DO UPDATE SET broker_id=excluded.broker_id,status=excluded.status,country=excluded.country,click_id=excluded.click_id,registration_date=excluded.registration_date,first_fund_date=excluded.first_fund_date,first_fund_amount=excluded.first_fund_amount,first_trade_date=excluded.first_trade_date,last_trade_date=excluded.last_trade_date,net_deposits=excluded.net_deposits,deposits=excluded.deposits,latest_balance=excluded.latest_balance,trading_volume=excluded.trading_volume"""
        account_sql = """INSERT INTO fxpro_accounts
        (login,email,name,country,jurisdiction,ib_group,registration_date,active,currency,usd,deposits,withdrawals,latest_balance,last_trade_date,label,phone)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(login) DO UPDATE SET label=excluded.label,phone=excluded.phone,email=excluded.email,name=excluded.name,country=excluded.country,jurisdiction=excluded.jurisdiction,ib_group=excluded.ib_group,registration_date=excluded.registration_date,active=excluded.active,currency=excluded.currency,usd=excluded.usd,deposits=excluded.deposits,withdrawals=excluded.withdrawals,latest_balance=excluded.latest_balance,last_trade_date=excluded.last_trade_date"""
        keys = ("email","broker_id","status","country","click_id","registration_date","first_fund_date","first_fund_amount","first_trade_date","last_trade_date","net_deposits","deposits","latest_balance","trading_volume")

        imported_clients = 0
        imported_accounts = 0
        imported_accounts_with_email = 0
        files_ok = 0
        file_types = []
        for file in files:
            raw = await file.read()
            accounts = parse_fxpro_clients_report(raw)
            if accounts:
                account_values = [
                    tuple(account.get(k) for k in ("login","email","name","country","jurisdiction","ib_group","registration_date","active","currency","usd","deposits","withdrawals","latest_balance","last_trade_date","label","phone"))
                    for account in accounts
                ]
                upsert_broker_accounts(conn, "FxPro", [{
                    "account_id": a["login"], "email": a.get("email"), "phone": a.get("phone"),
                    "label": a.get("label"), "name": a.get("name"), "country": a.get("country"),
                    "registration_date": a.get("registration_date"), "deposits": a.get("deposits"),
                    "withdrawals": a.get("withdrawals"), "balance": a.get("latest_balance"),
                    "last_trade_date": a.get("last_trade_date"),
                } for a in accounts])
                if account_values:
                    with conn.cursor() as cur:
                        cur.executemany(account_sql, account_values)
                imported_accounts += len(accounts)
                imported_accounts_with_email += sum(1 for account in accounts if account.get("email"))
                file_types.append("clients")
                files_ok += 1
                continue

            clients = parse_fxpro_report(raw)
            if clients:
                client_values = [tuple(client.get(k) for k in keys) for client in clients]
                if client_values:
                    with conn.cursor() as cur:
                        cur.executemany(sql, client_values)
                imported_clients += len(clients)
                file_types.append("detailed")
                files_ok += 1

        conn.commit()

        # Chatterfy is the authoritative event source. Do not push inferred
        # REG/FTD/FT events back into Chatterfy during FxPro imports.
        conn.execute("DELETE FROM crm_events WHERE source = 'fxpro'")
        conn.commit()
        linked_row = conn.execute("""
            SELECT COUNT(DISTINCT lower(fx.email)) AS c
            FROM (
                SELECT email FROM fxpro_accounts WHERE email IS NOT NULL AND email <> ''
                UNION ALL
                SELECT email FROM broker_clients WHERE email IS NOT NULL AND email <> ''
            ) fx
            WHERE EXISTS (
                SELECT 1 FROM chatterfy_leads l
                WHERE lower(l.email) = lower(fx.email)
            )
        """).fetchone()
        email_linked_clients = int(linked_row["c"] or 0)
        conn.close()
        conn = None
        chatterfy_push = push_after_import()
        return {"status":"ok","chatterfy_push":chatterfy_push,"broker":"FxPro","files":files_ok,"client_rows":imported_clients,"account_rows":imported_accounts,"accounts_with_email":imported_accounts_with_email,"rows":imported_clients + imported_accounts,"chatterfy_synced":0,"email_linked_clients":email_linked_clients,"file_types":file_types}
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=f"FxPro import failed: {type(exc).__name__}: {exc}")
    finally:
        if conn is not None:
            conn.close()

@app.get("/api/v1/clients/search")
def search_clients(q: str, x_telegram_username: str = Depends(current_username)):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    conn = db()
    needle = q.strip().lstrip("@").strip().lower()
    if not needle:
        conn.close()
        return {"clients": []}
    term = "%" + needle + "%"
    digits = re.sub(r"\D", "", needle)

    rows = conn.execute("""
        SELECT b.*, l.attribution_json AS chatterfy_attribution_json, l.click_id AS chatterfy_click_id
        FROM broker_clients b
        LEFT JOIN chatterfy_leads l ON lower(l.email) = lower(b.email)
        WHERE lower(b.email) LIKE %s
           OR lower(coalesce(b.click_id,'')) LIKE %s
           OR lower(coalesce(l.click_id,'')) LIKE %s
        LIMIT 20
    """, (term, term, term)).fetchall()

    account_rows = conn.execute("""
        SELECT *
        FROM fxpro_accounts
        WHERE lower(coalesce(email,'')) LIKE %s
           OR lower(login) LIKE %s
        ORDER BY registration_date DESC NULLS LAST
        LIMIT 100
    """, (term, term)).fetchall()

    # Chatterfy leads are searchable even before FxPro has a matching account.
    # This keeps a newly registered/FTD lead visible in CRM instead of returning
    # "client not found" simply because the broker report does not contain them.
    chatterfy_rows = conn.execute("""
        SELECT chat_id, email, click_id, attribution_json, uid, phone
        FROM chatterfy_leads
        ORDER BY chat_id DESC
    """).fetchall()

    # Pull Chatterfy attribution for the full-report-only accounts in one query.
    account_emails = sorted({(a["email"] or "").strip().lower() for a in account_rows if a["email"]})
    lead_by_email = {}
    if account_emails:
        placeholders = ",".join(["%s"] * len(account_emails))
        lead_rows = conn.execute(
            f"SELECT email, attribution_json, click_id FROM chatterfy_leads WHERE lower(email) IN ({placeholders})",
            tuple(account_emails),
        ).fetchall()
        for lead in lead_rows:
            email_key = (lead["email"] or "").strip().lower()
            current = lead_by_email.get(email_key)
            if current is None:
                lead_by_email[email_key] = lead

    import json

    def events_for_email(email_key):
        event_rows = conn.execute("""
            SELECT event_type, event_date, amount, source, broker_id, fxpro_login, chat_id, metadata_json
            FROM crm_events
            WHERE lower(email)=%s
            ORDER BY event_date ASC NULLS LAST, created_at ASC
        """, (email_key,)).fetchall()
        out = []
        for ev in event_rows:
            item = dict(ev)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
            item["type"] = item.pop("event_type")
            item["date"] = item.pop("event_date")
            out.append(item)
        return out

    def clean_item(item):
        raw_attr = item.pop("chatterfy_attribution_json", None)
        chatterfy_click_id = item.pop("chatterfy_click_id", None)
        try:
            attr = json.loads(raw_attr) if raw_attr else {}
        except Exception:
            attr = {}
        attr = {k: clean_attribution_value(v) for k, v in attr.items()}
        attr = {k: v for k, v in attr.items() if v is not None}
        item["attribution"] = attr
        if chatterfy_click_id and not item.get("click_id"):
            item["click_id"] = chatterfy_click_id
        item["campaign"] = attribution_text(attr, "tracker_campaign_name", "tracker_campaign")
        item["source"] = attribution_text(attr, "tracker_source_name", "tracker_source")
        item["adset"] = attribution_text(attr, "adset_name", "adset_id")
        item["ad"] = attribution_text(attr, "ad_id")
        item["placement"] = attribution_text(attr, "placement")
        item["chat_link"] = attr.get("chatlink")
        return item

    result = []
    seen_emails = set()

    for row in rows:
        item = clean_item(dict(row))
        email_key = (item.get("email") or "").strip().lower()
        seen_emails.add(email_key)
        item["fxpro_accounts"] = [
            dict(a) for a in account_rows
            if (a["email"] or "").strip().lower() == email_key
        ]
        # Always resolve FxPro accounts directly by normalized email as well.
        # This prevents a lead/broker row from hiding a real FxPro account when
        # the search term matched the client row but the account query used a
        # slightly different email representation.
        if not item["fxpro_accounts"] and email_key:
            direct_accounts = conn.execute("""
                SELECT * FROM fxpro_accounts
                WHERE lower(trim(coalesce(email,''))) = %s
                ORDER BY registration_date ASC NULLS LAST, login
            """, (email_key,)).fetchall()
            item["fxpro_accounts"] = [dict(a) for a in direct_accounts]
        item["fxpro_account_count"] = len(item["fxpro_accounts"])
        # Show broker account state on the card without turning account state
        # into REG/FTD/FT events. Events remain authoritative from Chatterfy.
        if item["fxpro_accounts"]:
            accounts = item["fxpro_accounts"]
            if not item.get("country"):
                item["country"] = next((a.get("country") for a in accounts if a.get("country")), None)
            if not item.get("registration_date"):
                dates = [a.get("registration_date") for a in accounts if a.get("registration_date")]
                item["registration_date"] = min(dates) if dates else None
            if item.get("latest_balance") is None:
                item["latest_balance"] = sum(float(a.get("latest_balance") or 0) for a in accounts)
            if item.get("deposits") is None:
                item["deposits"] = sum(float(a.get("deposits") or 0) for a in accounts)
            if item.get("net_deposits") is None:
                item["net_deposits"] = sum(float(a.get("deposits") or 0) - float(a.get("withdrawals") or 0) for a in accounts)
        # Expose a flat login list as a reliable UI fallback.
        item["fxpro_logins"] = [str(a.get("login")) for a in item["fxpro_accounts"] if a.get("login")]
        if user["role"] == "seo":
            for key in ("first_fund_amount", "net_deposits", "deposits", "latest_balance", "trading_volume"):
                item[key] = None
            for account in item["fxpro_accounts"]:
                for key in ("deposits", "withdrawals", "latest_balance", "usd"):
                    account[key] = None
        item["events"] = events_for_email((item.get("email") or "").strip().lower())
        # Event timeline is authoritative from Chatterfy/broker events only.
        # Do not synthesize REG/FTD/FT from FxPro report fields here.
        result.append(item)

    # If a lead exists in Chatterfy but is not present in either FxPro
    # report, synthesize a lead-only client card. When FxPro appears later,
    # the same Email will naturally resolve to the broker-backed card above.
    account_email_set = {(a["email"] or "").strip().lower() for a in account_rows if a["email"]}
    lead_extra_by_email = {}
    for lead in chatterfy_rows:
        email_key = (lead["email"] or "").strip().lower()
        try:
            attr = json.loads(lead["attribution_json"]) if lead["attribution_json"] else {}
        except Exception:
            attr = {}
        attr = {k: clean_attribution_value(v) for k, v in attr.items()}
        attr = {k: v for k, v in attr.items() if v is not None}
        # A JSON hit only counts when it is in the name / username, not in
        # some unrelated tracker field.
        folded = fold_text(needle)
        direct_hit = any(folded in fold_text(v) for v in (
            lead["email"], lead["click_id"], lead["uid"], lead["chat_id"], attr.get("name"), attr.get("username")))
        if not direct_hit and not (len(digits) >= 5 and digits in re.sub(r"\D", "", str(lead["phone"] or ""))):
            continue
        lead_extra = {
            "lead_key": lead_key_for(email_key, lead["chat_id"]),
            "name": attr.get("name"),
            "tg_username": attr.get("username"),
            "uid": lead["uid"],
            "phone": lead["phone"],
        }
        # If FxPro has this email, let the account-backed card below win so
        # the CRM shows the real FxPro login(s) instead of a lead-only card.
        if email_key:
            lead_extra_by_email.setdefault(email_key, lead_extra)
        if email_key and (email_key in seen_emails or email_key in account_email_set):
            lead_by_email.setdefault(email_key, lead)
            continue
        seen_key = email_key or lead_extra["lead_key"]
        if seen_key in seen_emails:
            continue
        item = {
            **lead_extra,
            "email": email_key or None,
            "broker_id": None,
            "status": "Chatterfy lead",
            "country": None,
            "click_id": lead["click_id"],
            "registration_date": None,
            "first_fund_date": None,
            "first_fund_amount": None,
            "first_trade_date": None,
            "last_trade_date": None,
            "net_deposits": None,
            "deposits": None,
            "latest_balance": None,
            "trading_volume": None,
            "fxpro_accounts": [],
            "fxpro_account_count": 0,
            "attribution": attr,
            "campaign": attribution_text(attr, "tracker_campaign_name", "tracker_campaign"),
            "source": attribution_text(attr, "tracker_source_name", "tracker_source"),
            "adset": attribution_text(attr, "adset_name", "adset_id"),
            "ad": attribution_text(attr, "ad_id"),
            "placement": attribution_text(attr, "placement"),
            "chat_link": attr.get("chatlink"),
            "events": events_for_email(email_key if email_key else f"chat:{lead['chat_id']}"),
        }
        result.append(item)
        seen_emails.add(seen_key)

    # If a client exists only in the full FxPro report, synthesize a client card
    # from all accounts sharing that Email. This handles multiple FxPro logins
    # without overwriting or double-counting the accounts.
    grouped = {}
    for account in account_rows:
        email_key = (account["email"] or "").strip().lower()
        if email_key and email_key not in seen_emails:
            grouped.setdefault(email_key, []).append(dict(account))

    for email_key, accounts in grouped.items():
        reg_dates = [a["registration_date"] for a in accounts if a["registration_date"]]
        trade_dates = [a["last_trade_date"] for a in accounts if a["last_trade_date"]]
        deposits = sum(float(a["deposits"] or 0) for a in accounts)
        withdrawals = sum(float(a["withdrawals"] or 0) for a in accounts)
        balance = sum(float(a["latest_balance"] or 0) for a in accounts)
        lead = lead_by_email.get(email_key)
        attr = {}
        click_id = None
        if lead:
            try:
                attr = json.loads(lead["attribution_json"]) if lead["attribution_json"] else {}
            except Exception:
                attr = {}
            attr = {k: clean_attribution_value(v) for k, v in attr.items()}
            attr = {k: v for k, v in attr.items() if v is not None}
            click_id = lead["click_id"]

        first = accounts[0]
        item = {
            "email": email_key,
            "broker_id": None,
            "status": "FxPro account",
            "country": first.get("country"),
            "click_id": click_id,
            "registration_date": min(reg_dates) if reg_dates else None,
            # The full account report has total deposits, not a true first-fund
            # event. Keep this as a funded summary rather than pretending it is
            # an exact FTD date.
            "first_fund_date": None,
            "first_fund_amount": deposits if deposits > 0 else None,
            "first_trade_date": min(trade_dates) if trade_dates else None,
            "last_trade_date": max(trade_dates) if trade_dates else None,
            "net_deposits": deposits - withdrawals,
            "deposits": deposits,
            "latest_balance": balance,
            "trading_volume": None,
            "fxpro_accounts": accounts,
            "fxpro_account_count": len(accounts),
            "attribution": attr,
            "campaign": attribution_text(attr, "tracker_campaign_name", "tracker_campaign"),
            "source": attribution_text(attr, "tracker_source_name", "tracker_source"),
            "adset": attribution_text(attr, "adset_name", "adset_id"),
            "ad": attribution_text(attr, "ad_id"),
            "placement": attribution_text(attr, "placement"),
            "chat_link": attr.get("chatlink"),
            "events": events_for_email(email_key),
        }
        # Keep event timeline strictly event-driven. The full FxPro account
        # report contains account state (registration/total deposits/last trade),
        # not authoritative broker events, so do not manufacture CRM events here.
        if user["role"] == "seo":
            for key in ("first_fund_amount", "net_deposits", "deposits", "latest_balance", "trading_volume"):
                item[key] = None
            for account in item["fxpro_accounts"]:
                for key in ("deposits", "withdrawals", "latest_balance", "usd"):
                    account[key] = None
        result.append(item)

    conn.close()
    # Give every card the Chatterfy identity (name, @username, UID, lead card link) when known.
    for item in result:
        extra = lead_extra_by_email.get((item.get("email") or "").strip().lower())
        if extra:
            for k, v in extra.items():
                if item.get(k) is None:
                    item[k] = v
    return {"clients": result[:20]}


@app.get("/api/v1/clients/debug")
def debug_client_link(q: str, x_telegram_username: str = Depends(current_username)):
    """Admin-only diagnostic for the client -> Chatterfy -> FxPro link."""
    conn = db()
    try:
        require_admin(x_telegram_username, conn)
        raw = (q or "").strip()
        email = raw.lower()
        if "@" not in email:
            account = conn.execute("SELECT email FROM fxpro_accounts WHERE lower(trim(login))=%s LIMIT 1", (email,)).fetchone()
            email = (account["email"] or "").strip().lower() if account else email
        broker = conn.execute("SELECT * FROM broker_clients WHERE lower(trim(email))=%s", (email,)).fetchall()
        leads = conn.execute("SELECT chat_id,email,click_id,last_synced_event,attribution_json FROM chatterfy_leads WHERE lower(trim(coalesce(email,'')))=%s ORDER BY chat_id", (email,)).fetchall()
        accounts = conn.execute("SELECT login,email,name,country,registration_date,deposits,withdrawals,latest_balance,last_trade_date FROM fxpro_accounts WHERE lower(trim(coalesce(email,'')))=%s ORDER BY login", (email,)).fetchall()
        events = conn.execute("SELECT event_type,event_date,amount,source,broker_id,fxpro_login,chat_id FROM crm_events WHERE lower(trim(coalesce(email,'')))=%s ORDER BY event_date NULLS LAST, created_at", (email,)).fetchall()
        return {
            "query": raw,
            "normalized_email": email,
            "broker_clients": [dict(r) for r in broker],
            "chatterfy_leads": [dict(r) for r in leads],
            "fxpro_accounts": [dict(r) for r in accounts],
            "crm_events": [dict(r) for r in events],
            "counts": {"broker_clients": len(broker), "chatterfy_leads": len(leads), "fxpro_accounts": len(accounts), "crm_events": len(events)},
        }
    finally:
        conn.close()


@app.get("/api/v1/traffic")
def traffic(x_telegram_username: str = Depends(current_username)):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    conn = db()
    try:
        leads = conn.execute("SELECT chat_id,email,click_id,attribution_json FROM chatterfy_leads").fetchall()
        events = conn.execute("SELECT email,event_type,event_date,amount,chat_id FROM crm_events WHERE source='chatterfy'").fetchall()
    finally:
        conn.close()

    by_email, by_chat = {}, {}
    for ev in events:
        et = str(ev["event_type"] or "").upper()
        if et not in STAGE_ORDER:
            continue
        if ev["email"] and not str(ev["email"]).startswith("chat:"):
            by_email.setdefault(str(ev["email"]).strip().lower(), []).append(ev)
        if ev["chat_id"]:
            by_chat.setdefault(str(ev["chat_id"]), []).append(ev)

    grouped = {}
    for lead in leads:
        email = (lead["email"] or "").strip().lower()
        evs = list(by_email.get(email, [])) if email else []
        evs.extend(by_chat.get(str(lead["chat_id"]), []))
        types, ftd_amount = set(), 0.0
        for ev in evs:
            et = str(ev["event_type"] or "").upper()
            if et in STAGE_ORDER:
                types.add(et)
                if et == "FTD" and ev["amount"] is not None:
                    ftd_amount = max(ftd_amount, float(ev["amount"] or 0))

        try:
            attr = json.loads(lead["attribution_json"]) if lead["attribution_json"] else {}
        except Exception:
            attr = {}
        attr = {k: clean_attribution_value(v) for k, v in attr.items()}
        attr = {k: v for k, v in attr.items() if v is not None}
        campaign = attribution_text(attr, "tracker_campaign_name", "tracker_campaign") or "Unknown campaign"
        source = attribution_text(attr, "tracker_source_name", "tracker_source") or "Unknown source"
        adset = attribution_text(attr, "adset_name", "adset_id") or "Unknown adset"
        ad = attribution_text(attr, "ad_id") or "Unknown ad"
        placement = attribution_text(attr, "placement") or "Unknown placement"
        click = attribution_text(attr, "clickid") or lead["click_id"] or "No Click ID"
        key = (campaign, source, adset, ad, placement)
        g = grouped.setdefault(key, {"campaign": campaign, "source": source, "adset": adset, "ad": ad, "placement": placement, "leads": 0, "reg": 0, "ftd": 0, "ft": 0, "deposits": 0.0, "clicks": set()})
        g["leads"] += 1
        g["reg"] += int("REG" in types)
        g["ftd"] += int("FTD" in types)
        g["ft"] += int("FT" in types)
        g["deposits"] += ftd_amount
        g["clicks"].add(str(click))

    result = []
    for g in grouped.values():
        g["clicks"] = len(g["clicks"])
        g["reg_to_ftd"] = round(g["ftd"] / g["reg"] * 100, 1) if g["reg"] else 0
        g["ftd_to_ft"] = round(g["ft"] / g["ftd"] * 100, 1) if g["ftd"] else 0
        result.append(g)
    result.sort(key=lambda x: (x["ftd"], x["deposits"], x["leads"]), reverse=True)
    return {"rows": result, "total": len(result), "viewer": user["role"]}

@app.get("/api/v1/finance")
def finance(x_telegram_username: str = Depends(current_username)):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    if user["role"] not in ("admin", "head_buying"):
        raise HTTPException(status_code=403, detail="Finance access required")

    conn = db()
    try:
        accounts = conn.execute("""
            SELECT country, deposits, withdrawals, latest_balance, usd
            FROM fxpro_accounts
        """).fetchall()
        ftd_events = conn.execute("""
            SELECT email,chat_id,amount
            FROM crm_events
            WHERE source='chatterfy' AND upper(event_type)='FTD'
        """).fetchall()
    finally:
        conn.close()

    account_deposits = sum(float(r["deposits"] or 0) for r in accounts)
    withdrawals = sum(float(r["withdrawals"] or 0) for r in accounts)
    balance = sum(float(r["latest_balance"] or 0) for r in accounts)
    account_net = account_deposits - withdrawals

    # FTD is an authoritative Chatterfy funnel event; FxPro supplies account-level cash state.
    ftd_keys = set()
    ftd_deposits = 0.0
    for r in ftd_events:
        key = ((str(r["email"]).strip().lower() if r["email"] else "") or f"chat:{r['chat_id']}")
        if key in ftd_keys:
            continue
        ftd_keys.add(key)
        ftd_deposits += float(r["amount"] or 0)
    ftd_count = len(ftd_keys)

    geo = {}
    for r in accounts:
        country = (r["country"] or "Unknown").strip() or "Unknown"
        g = geo.setdefault(country, {"country": country, "deposits": 0.0, "withdrawals": 0.0, "net": 0.0, "balance": 0.0})
        g["deposits"] += float(r["deposits"] or 0)
        g["withdrawals"] += float(r["withdrawals"] or 0)
        g["net"] += float(r["deposits"] or 0) - float(r["withdrawals"] or 0)
        g["balance"] += float(r["latest_balance"] or 0)

    return {
        "accounts": len(accounts),
        "deposits": account_deposits,
        "withdrawals": withdrawals,
        "net_deposits": account_net,
        "balance": balance,
        "ftd_count": ftd_count,
        "ftd_deposits": ftd_deposits,
        "avg_ftd": (ftd_deposits / ftd_count) if ftd_count else 0,
        "geo": sorted(geo.values(), key=lambda x: x["net"], reverse=True)[:15],
        "source": "FxPro account report",
        "note": "Account-level deposits/withdrawals come from FxPro clients reports; FTD metrics come from matched broker clients.",
    }

@app.get("/api/v1/operations")
def operations(x_telegram_username: str = Depends(current_username)):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Operations access required")

    conn = db()
    try:
        counts = conn.execute("""
            SELECT
              (SELECT count(*) FROM chatterfy_leads) AS chatterfy_leads,
              (SELECT count(DISTINCT lower(trim(email))) FROM chatterfy_leads WHERE email IS NOT NULL AND trim(email) <> '') AS chatterfy_emails,
              (SELECT count(DISTINCT lower(trim(email))) FROM fxpro_accounts WHERE email IS NOT NULL AND trim(email) <> '') AS fxpro_emails,
              (SELECT count(DISTINCT lower(trim(l.email)))
                 FROM chatterfy_leads l
                 JOIN fxpro_accounts a ON lower(trim(a.email)) = lower(trim(l.email))
                WHERE l.email IS NOT NULL AND trim(l.email) <> '') AS linked_emails,
              (SELECT count(*) FROM crm_events) AS events
        """).fetchone()
        unmatched = conn.execute("""
            SELECT l.email, l.chat_id, l.click_id, l.attribution_json
            FROM chatterfy_leads l
            LEFT JOIN (
              SELECT DISTINCT lower(trim(email)) AS email
              FROM fxpro_accounts
              WHERE email IS NOT NULL AND trim(email) <> ''
            ) a ON a.email = lower(trim(l.email))
            WHERE a.email IS NULL
            ORDER BY l.chat_id DESC
            LIMIT 30
        """).fetchall()
        events = conn.execute("""
            SELECT email,event_type,event_date,amount,source,broker_id,fxpro_login,chat_id,created_at
            FROM crm_events
            ORDER BY created_at DESC
            LIMIT 30
        """).fetchall()
    finally:
        conn.close()

    import json
    pending = []
    for r in unmatched:
        try:
            attr = json.loads(r["attribution_json"]) if r["attribution_json"] else {}
        except Exception:
            attr = {}
        pending.append({
            "email": r["email"] or f"Telegram {r['chat_id']}",
            "chat_id": r["chat_id"],
            "click_id": r["click_id"],
            "campaign": attribution_text(attr, "tracker_campaign_name", "tracker_campaign"),
            "source": attribution_text(attr, "tracker_source_name", "tracker_source"),
        })
    c = dict(counts)
    return {
        "counts": {
            "chatterfy_leads": int(c["chatterfy_leads"] or 0),
            "chatterfy_emails": int(c["chatterfy_emails"] or 0),
            "fxpro_emails": int(c["fxpro_emails"] or 0),
            "linked_emails": int(c["linked_emails"] or 0),
            "unmatched_emails": max(int(c["chatterfy_emails"] or 0) - int(c["linked_emails"] or 0), 0),
            "events": int(c["events"] or 0),
        },
        "unmatched": pending,
        "events": [dict(r) for r in events],
    }


@app.get("/api/v1/alerts")
def alerts(x_telegram_username: str = Depends(current_username)):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    if user["role"] not in ("admin", "head_buying", "handler"):
        raise HTTPException(status_code=403, detail="Alerts access required")
    conn = db()
    try:
        leads = conn.execute("SELECT email, chat_id, click_id, attribution_json FROM chatterfy_leads ORDER BY chat_id DESC LIMIT 100").fetchall()
        accounts = conn.execute("SELECT DISTINCT lower(trim(email)) AS email FROM fxpro_accounts WHERE email IS NOT NULL AND trim(email) <> ''").fetchall()
        events = conn.execute("SELECT email, event_type, event_date, amount, broker_id, fxpro_login, chat_id, created_at FROM crm_events WHERE source = 'chatterfy' ORDER BY created_at DESC LIMIT 30").fetchall()
    finally:
        conn.close()
    account_emails = {str(r["email"]).lower() for r in accounts if r["email"]}
    import json
    pending = []
    missing_attribution = []
    for row in leads:
        try:
            attr = json.loads(row["attribution_json"]) if row["attribution_json"] else {}
        except Exception:
            attr = {}
        attr = {k: clean_attribution_value(v) for k, v in attr.items()}
        campaign = attribution_text(attr, "tracker_campaign_name", "tracker_campaign")
        source = attribution_text(attr, "tracker_source_name", "tracker_source")
        click = attribution_text(attr, "clickid") or row["click_id"]
        item = {"email": row["email"] or f"Telegram {row['chat_id']}", "chat_id": row["chat_id"], "click_id": click, "campaign": campaign, "source": source}
        if not row["email"] or row["email"].strip().lower() not in account_emails:
            pending.append(item)
        if not click and not campaign and not source:
            missing_attribution.append({"email": row["email"], "chat_id": row["chat_id"]})
    unlinked_events = []
    missing_amount = []
    for row in events:
        item = dict(row)
        if (row["email"] or "").strip().lower() not in account_emails:
            unlinked_events.append(item)
        if str(row["event_type"] or "").upper() == "FTD" and (row["amount"] is None or float(row["amount"] or 0) <= 0):
            missing_amount.append(item)
    return {"counts": {"pending_fxpro": len(pending), "missing_attribution": len(missing_attribution), "unlinked_events": len(unlinked_events), "ftd_missing_amount": len(missing_amount), "broker_events": len(events)}, "pending_fxpro": pending[:30], "missing_attribution": missing_attribution[:30], "unlinked_events": unlinked_events[:30], "ftd_missing_amount": missing_amount[:30], "broker_events": [dict(r) for r in events[:10]]}


# Alerts module
@app.get("/api/v1/dashboard")
def dashboard(days: int = 0, x_telegram_username: str = Depends(current_username)):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    conn = db()
    try:
        lead_rows = conn.execute("""
            SELECT chat_id,email,click_id,attribution_json,first_seen_at,updated_at
            FROM chatterfy_leads
            ORDER BY first_seen_at DESC NULLS LAST, chat_id DESC
        """).fetchall()
        event_rows = conn.execute("""
            SELECT email,event_type,event_date,amount,chat_id,created_at
            FROM crm_events
            WHERE source='chatterfy'
            ORDER BY event_date ASC NULLS LAST, created_at ASC
        """).fetchall()
        account_rows = conn.execute("""
            SELECT lower(trim(email)) AS email,country
            FROM fxpro_accounts
            WHERE email IS NOT NULL AND trim(email) <> ''
        """).fetchall()
    finally:
        conn.close()

    cutoff = (datetime.utcnow() - timedelta(days=days)) if days and days > 0 else None
    leads = []
    for row in lead_rows:
        if cutoff and row["first_seen_at"]:
            try:
                if datetime.fromisoformat(str(row["first_seen_at"]).replace("Z", "+00:00")).replace(tzinfo=None) < cutoff:
                    continue
            except Exception:
                pass
        leads.append(row)

    def lead_key(email, chat_id):
        return (str(email).strip().lower() if email else "") or f"chat:{chat_id}"

    by_email = {}
    by_chat = {}
    for ev in event_rows:
        et = str(ev["event_type"] or "").upper()
        if et not in STAGE_ORDER:
            continue
        if ev["email"] and not str(ev["email"]).startswith("chat:"):
            by_email.setdefault(str(ev["email"]).strip().lower(), []).append(ev)
        if ev["chat_id"]:
            by_chat.setdefault(str(ev["chat_id"]), []).append(ev)

    def stage_for_lead(row):
        email = (row["email"] or "").strip().lower()
        events = list(by_email.get(email, [])) if email else []
        events.extend(by_chat.get(str(row["chat_id"]), []))
        types, dates, amount = set(), {}, 0.0
        for ev in events:
            et = str(ev["event_type"] or "").upper()
            if et not in STAGE_ORDER:
                continue
            if cutoff and ev["event_date"]:
                try:
                    if datetime.fromisoformat(str(ev["event_date"]).replace("Z", "+00:00")).replace(tzinfo=None) < cutoff:
                        continue
                except Exception:
                    pass
            types.add(et)
            if ev["event_date"] and (et not in dates or str(ev["event_date"]) < str(dates[et])):
                dates[et] = ev["event_date"]
            if et == "FTD" and ev["amount"] is not None:
                amount = max(amount, float(ev["amount"] or 0))
        return {"types": types, "dates": dates, "amount": amount}

    account_country = {}
    for row in account_rows:
        account_country.setdefault(row["email"], row["country"])

    lead_states = [(row, stage_for_lead(row)) for row in leads]
    total = len(leads)
    reg = sum("REG" in state["types"] for _, state in lead_states)
    ftd = sum("FTD" in state["types"] for _, state in lead_states)
    ft = sum("FT" in state["types"] for _, state in lead_states)
    deposits = sum(state["amount"] for _, state in lead_states if "FTD" in state["types"])

    countries, geo_stats, daily_stats, attribution_stats = {}, {}, {}, {}
    for row, state in lead_states:
        email = (row["email"] or "").strip().lower()
        country = (account_country.get(email) or "Unknown").strip() or "Unknown"
        countries[country] = countries.get(country, 0) + 1
        g = geo_stats.setdefault(country, {"country": country, "leads": 0, "reg": 0, "ftd": 0, "ft": 0, "deposits": 0.0})
        g["leads"] += 1
        g["reg"] += int("REG" in state["types"])
        g["ftd"] += int("FTD" in state["types"])
        g["ft"] += int("FT" in state["types"])
        g["deposits"] += state["amount"]

        for et in ("REG", "FTD", "FT"):
            date_value = state["dates"].get(et)
            if date_value:
                d = daily_stats.setdefault(str(date_value)[:10], {"date": str(date_value)[:10], "reg": 0, "ftd": 0, "ft": 0, "deposits": 0.0})
                d[et.lower()] += 1
                if et == "FTD":
                    d["deposits"] += state["amount"]

        try:
            attr = json.loads(row["attribution_json"]) if row["attribution_json"] else {}
        except Exception:
            attr = {}
        attr = {k: clean_attribution_value(v) for k, v in attr.items()}
        attr = {k: v for k, v in attr.items() if v is not None}
        campaign = attribution_text(attr, "tracker_campaign_name", "tracker_campaign") or "Unknown campaign"
        source = attribution_text(attr, "tracker_source_name", "tracker_source") or "Unknown source"
        adset = attribution_text(attr, "adset_name", "adset_id") or "Unknown adset"
        ad = attribution_text(attr, "ad_id") or "Unknown ad"
        placement = attribution_text(attr, "placement") or "Unknown placement"
        click = attribution_text(attr, "clickid") or row["click_id"] or "No Click ID"
        key = (campaign, source, adset, ad, placement, str(click))
        bucket = attribution_stats.setdefault(key, {"campaign": campaign, "source": source, "adset": adset, "ad": ad, "placement": placement, "click_id": str(click), "leads": 0, "reg": 0, "ftd": 0, "ft": 0, "deposits": 0.0})
        bucket["leads"] += 1
        bucket["reg"] += int("REG" in state["types"])
        bucket["ftd"] += int("FTD" in state["types"])
        bucket["ft"] += int("FT" in state["types"])
        bucket["deposits"] += state["amount"]

    top_countries = sorted(countries.items(), key=lambda x: x[1], reverse=True)[:5]
    top_geo = sorted(geo_stats.values(), key=lambda x: (x["ftd"], x["deposits"], x["reg"]), reverse=True)[:12]
    top_attribution = sorted(attribution_stats.values(), key=lambda x: (x["ftd"], x["reg"], x["leads"]), reverse=True)[:12]
    for item in top_attribution:
        item["reg_to_ftd"] = round(item["ftd"] / item["reg"] * 100, 1) if item["reg"] else 0
        item["ftd_to_ft"] = round(item["ft"] / item["ftd"] * 100, 1) if item["ftd"] else 0

    recent = []
    for row, state in lead_states[:5]:
        recent.append({
            "email": row["email"] or f"Telegram {row['chat_id']}",
            "country": account_country.get((row["email"] or "").strip().lower()),
            "status": "FT" if "FT" in state["types"] else ("FTD" if "FTD" in state["types"] else ("REG" if "REG" in state["types"] else "LEAD")),
            "registration_date": state["dates"].get("REG"),
            "first_fund_date": state["dates"].get("FTD"),
            "first_trade_date": state["dates"].get("FT"),
            "first_fund_amount": None if user["role"] == "seo" else state["amount"],
            "click_id": row["click_id"],
        })

    return {
        "leads": total, "reg": reg, "ftd": ftd, "ft": ft,
        "deposits": deposits if user["role"] != "seo" else None,
        "viewer": {"username": user["username"], "role": user["role"]},
        "period_days": days if cutoff else 0,
        "funnel": {
            "reg_to_ftd": round(ftd / reg * 100, 1) if reg else 0,
            "ftd_to_ft": round(ft / ftd * 100, 1) if ftd else 0,
            "reg_to_ft": round(ft / reg * 100, 1) if reg else 0,
        },
        "top_countries": [{"country": k, "count": v} for k, v in top_countries],
        "geo": top_geo,
        "daily": sorted(daily_stats.values(), key=lambda x: x["date"])[-30:],
        "company": [{"name": "FxPro", "clients": total, "reg": reg, "ftd": ftd, "ft": ft, "deposits": deposits if user["role"] != "seo" else None, "net_deposits": None}],
        "operations": {"clients": total, "attributed_clients": sum(1 for row in leads if row["attribution_json"]), "unattributed_clients": sum(1 for row in leads if not row["attribution_json"]), "fxpro_accounts": len(account_rows)},
        "chatterfy": {"tracker": "Chatterfy", "attribution": top_attribution, "matched_clients": sum(1 for row in leads if (row["email"] or "").strip().lower() in account_country)},
        "recent": recent,
    }

# ---------------------------------------------------------------------------
# Leads workspace: one row per Chatterfy lead (deduplicated by email), with
# funnel stage from Chatterfy events and the handler workflow on top.
# ---------------------------------------------------------------------------

LEAD_ROLES = ("admin", "head_buying", "handler")
LEAD_MANAGER_ROLES = ("admin", "head_buying")


def lead_key_for(email, chat_id):
    email = (email or "").strip().lower()
    return email if email else f"chat:{chat_id}"


def _load_attr(raw):
    try:
        attr = json.loads(raw) if raw else {}
    except Exception:
        attr = {}
    attr = {k: clean_attribution_value(v) for k, v in (attr or {}).items()}
    return {k: v for k, v in attr.items() if v is not None}


def _attr_score(attr):
    return sum(1 for k in ("tracker_campaign", "tracker_source", "tracker_campaign_name", "tracker_source_name",
                           "adset_id", "adset_name", "ad_id", "placement", "clickid") if attr.get(k))


def load_broker_accounts(conn, broker=None):
    """All broker accounts for matching, including FxPro detailed-report clients."""
    rows = conn.execute("SELECT * FROM broker_accounts").fetchall()
    accounts = [dict(r) for r in rows]
    # The FxPro detailed report (broker_clients) carries ClickID and real FTD
    # date/amount, so it is a first-class source for matching too.
    for r in conn.execute("SELECT * FROM broker_clients").fetchall():
        email = broker_lib.normalize_email(r["email"])
        accounts.append({
            "broker": "FxPro", "account_id": r["broker_id"] or email, "email": email, "phone": None,
            "label": broker_lib.normalize_label(r["click_id"]), "name": None, "country": r["country"],
            "registration_date": r["registration_date"], "ftd_date": r["first_fund_date"],
            "ftd_amount": r["first_fund_amount"], "deposits": r["deposits"], "withdrawals": None,
            "balance": r["latest_balance"], "last_trade_date": r["last_trade_date"], "source": "detailed",
        })
    if broker:
        accounts = [a for a in accounts if a["broker"] == broker]
    return accounts


def build_leads(conn, viewer, accounts=None):
    """Return every lead as a dict. Small enough to filter in Python."""
    lead_rows = conn.execute(
        "SELECT chat_id, email, click_id, attribution_json, first_seen_at, updated_at, phone, uid FROM chatterfy_leads"
    ).fetchall()
    event_rows = conn.execute(
        "SELECT email, event_type, event_date, amount, created_at FROM crm_events WHERE source='chatterfy'"
    ).fetchall()
    if accounts is None:
        accounts = load_broker_accounts(conn)
    account_index = broker_lib.build_account_index(accounts)
    work = {r["lead_key"]: dict(r) for r in conn.execute("SELECT * FROM lead_work").fetchall()}
    note_counts = {
        r["lead_key"]: int(r["c"])
        for r in conn.execute("SELECT lead_key, COUNT(*) AS c FROM lead_notes GROUP BY lead_key").fetchall()
    }

    events_by_email = {}
    for ev in event_rows:
        events_by_email.setdefault((ev["email"] or "").strip().lower(), []).append(ev)

    hide_money = viewer["role"] not in LEAD_MANAGER_ROLES
    leads = {}
    for r in lead_rows:
        key = lead_key_for(r["email"], r["chat_id"])
        attr = _load_attr(r["attribution_json"])
        current = leads.get(key)
        seen = r["first_seen_at"] or attr.get("created_at")
        if current is None:
            current = leads[key] = {
                "lead_key": key,
                "email": (r["email"] or "").strip().lower() or None,
                "chat_ids": [],
                "click_id": None,
                "attr": {},
                "first_seen_at": seen,
                "updated_at": r["updated_at"],
                "phone": None,
                "uid": None,
            }
        current["chat_ids"].append(str(r["chat_id"]))
        current["phone"] = current["phone"] or r["phone"] or broker_lib.normalize_phone(attr.get("phone"))
        current["uid"] = current["uid"] or r["uid"]
        if _attr_score(attr) >= _attr_score(current["attr"]):
            current["attr"] = {**current["attr"], **attr}
        current["click_id"] = current["click_id"] or attribution_text(attr, "clickid") or r["click_id"]
        if seen and (not current["first_seen_at"] or seen < current["first_seen_at"]):
            current["first_seen_at"] = seen
        if r["updated_at"] and (not current["updated_at"] or r["updated_at"] > current["updated_at"]):
            current["updated_at"] = r["updated_at"]

    result = []
    for key, item in leads.items():
        attr = item.pop("attr")
        evs = list(events_by_email.get(item["email"] or "", [])) if item["email"] else []
        for cid in item["chat_ids"]:
            evs += events_by_email.get(f"chat:{cid}", [])
        stage = "LEAD"
        ftd_amount = None
        last_event_at = None
        for ev in evs:
            t = str(ev["event_type"] or "").upper()
            if STAGE_ORDER.get(t, -1) > STAGE_ORDER[stage]:
                stage = t
            if t == "FTD" and ev["amount"] is not None:
                ftd_amount = float(ev["amount"])
            stamp = ev["created_at"] or ev["event_date"]
            if stamp and (not last_event_at or stamp > last_event_at):
                last_event_at = stamp
        w = work.get(key, {})
        labels = [x for x in [item["click_id"], *item["chat_ids"]] if x]
        method, matched = broker_lib.match_lead({"account_ids": [item["uid"]] if item["uid"] else [], "labels": labels,
                                                 "email": item["email"], "phone": item["phone"]}, account_index)
        activity = max(x for x in (item["updated_at"], last_event_at, w.get("updated_at"), item["first_seen_at"], "") if x is not None)
        result.append({
            **item,
            "name": attr.get("name"),
            "tg_username": attr.get("username"),
            "tags": attr.get("tags"),
            "campaign": attribution_text(attr, "tracker_campaign_name", "tracker_campaign", "campaign_name", "utm_campaign"),
            "source": attribution_text(attr, "tracker_source_name", "tracker_source", "utm_source"),
            "chat_link": attr.get("chatlink"),
            "stage": stage,
            "ftd_amount": None if hide_money else ftd_amount,
            "fxpro_linked": bool(matched),
            "broker_match": method,
            "brokers": sorted({a["broker"] for a in matched}),
            "matched_accounts": matched,
            "assignee": w.get("assignee"),
            "work_status": w.get("work_status") or "new",
            "callback_at": w.get("callback_at"),
            "notes": note_counts.get(key, 0),
            "last_activity": activity or None,
        })
    result.sort(key=lambda x: x["last_activity"] or "", reverse=True)
    return result


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


_FOLD = str.maketrans({"ş": "s", "ç": "c", "ğ": "g", "ı": "i", "ö": "o", "ü": "u", "â": "a", "î": "i", "û": "u"})


def fold_text(value):
    """Lowercase and drop Turkish diacritics so "ayse" finds "Ayşe" and "celik" finds "Çelik"."""
    return str(value or "").replace("İ", "i").replace("I", "ı").lower().translate(_FOLD)


def lead_matches(x, needle):
    """Free-text lead search: email, name, @username, UID, phone, Click ID, chat id, tags, campaign."""
    if not needle:
        return True
    needle = fold_text(needle)
    hay = (x.get("email"), x.get("click_id"), x.get("name"), x.get("tg_username"), x.get("campaign"),
           " ".join(x.get("chat_ids") or []), x.get("uid"), x.get("tags"), x.get("phone"))
    if any(needle in fold_text(v) for v in hay):
        return True
    digits = _digits(needle)
    return len(digits) >= 5 and digits in _digits(x.get("phone"))


def _lead_row(x):
    return {k: v for k, v in x.items() if k != "matched_accounts"}


def require_lead_access(username, conn):
    user = require_access(username, conn)
    if user["role"] not in LEAD_ROLES:
        raise HTTPException(status_code=403, detail="Leads access required")
    return user


@app.get("/api/v1/leads")
def list_leads(
    q: str = "", stage: str = "", work_status: str = "", campaign: str = "",
    assignee: str = "", days: int = 0, limit: int = 50, offset: int = 0,
    x_telegram_username: str = Depends(current_username),
):
    conn = db()
    try:
        user = require_lead_access(x_telegram_username, conn)
        leads = build_leads(conn, user)
        team = [
            dict(r) for r in conn.execute(
                "SELECT username, role FROM crm_users WHERE active=TRUE AND role IN ('handler','admin','head_buying') ORDER BY username"
            ).fetchall()
        ]
    finally:
        conn.close()

    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() if days and days > 0 else None
    needle = q.strip().lstrip("@").lower()
    me = user["username"].lower()

    def match(x, skip=None):
        if needle and not lead_matches(x, needle):
            return False
        if cutoff and (x["first_seen_at"] or x["last_activity"] or "") < cutoff:
            return False
        if campaign and x["campaign"] != campaign:
            return False
        if skip != "stage" and stage and x["stage"] != stage.upper():
            return False
        if work_status and x["work_status"] != work_status:
            return False
        if assignee == "me" and (x["assignee"] or "").lower() != me:
            return False
        if assignee == "none" and x["assignee"]:
            return False
        if assignee not in ("", "me", "none") and (x["assignee"] or "").lower() != assignee.lower():
            return False
        return True

    base = [x for x in leads if match(x, skip="stage")]
    stage_counts = {s: 0 for s in STAGE_ORDER}
    for x in base:
        stage_counts[x["stage"]] += 1
    filtered = [x for x in base if match(x)]
    limit = max(1, min(limit, 200))
    page = [{k: v for k, v in x.items() if k != "matched_accounts"} for x in filtered[offset:offset + limit]]
    return {
        "total": len(filtered),
        "rows": page,
        "stage_counts": stage_counts,
        "campaigns": sorted({x["campaign"] for x in leads if x["campaign"]}),
        "team": team,
        "work_statuses": list(WORK_STATUSES),
        "can_assign": user["role"] in LEAD_MANAGER_ROLES,
        "viewer": {"username": user["username"], "role": user["role"]},
    }


@app.get("/api/v1/leads/today")
def leads_today(x_telegram_username: str = Depends(current_username)):
    """Handler's to-do list: own leads grouped by what to do next, plus free leads to take."""
    conn = db()
    try:
        user = require_lead_access(x_telegram_username, conn)
        leads = build_leads(conn, user)
    finally:
        conn.close()

    me = user["username"].lower()
    now = datetime.utcnow()
    today = now.date().isoformat()
    stale_before = (now - timedelta(hours=48)).isoformat()
    closed = ("won", "lost")

    mine = [x for x in leads if (x["assignee"] or "").lower() == me]
    buckets = {"callback": [], "reg_no_ftd": [], "no_answer": [], "stale": [], "active": []}
    for x in mine:
        if x["work_status"] in closed or x["stage"] in ("FTD", "FT"):
            continue
        if x["work_status"] == "callback":
            buckets["callback"].append(x)
        elif x["stage"] == "REG":
            buckets["reg_no_ftd"].append(x)
        elif x["work_status"] == "no_answer":
            buckets["no_answer"].append(x)
        elif (x["last_activity"] or "") < stale_before:
            buckets["stale"].append(x)
        else:
            buckets["active"].append(x)

    buckets["callback"].sort(key=lambda x: x.get("callback_at") or "9999")
    free = [x for x in leads if not x["assignee"] and x["work_status"] == "new" and x["stage"] in ("LEAD", "REG")]
    free.sort(key=lambda x: x["first_seen_at"] or x["last_activity"] or "", reverse=True)

    def take(rows, n=30):
        return {"count": len(rows), "rows": [_lead_row(x) for x in rows[:n]]}

    return {
        "viewer": {"username": user["username"], "role": user["role"]},
        "stats": {
            "mine": len(mine),
            "in_work": sum(len(v) for v in buckets.values()),
            "mine_reg": sum(1 for x in mine if x["stage"] == "REG"),
            "mine_ftd": sum(1 for x in mine if x["stage"] in ("FTD", "FT")),
            "new_today": sum(1 for x in leads if (x["first_seen_at"] or "")[:10] == today),
            "free": len(free),
        },
        "callback": take(buckets["callback"]),
        "reg_no_ftd": take(buckets["reg_no_ftd"]),
        "no_answer": take(buckets["no_answer"]),
        "stale": take(buckets["stale"]),
        "active": take(buckets["active"]),
        "free": take(free, 15),
    }


@app.get("/api/v1/leads/{lead_key:path}/detail")
def lead_detail(lead_key: str, x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        user = require_lead_access(x_telegram_username, conn)
        lead = next((x for x in build_leads(conn, user) if x["lead_key"] == lead_key), None)
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
        notes = conn.execute(
            "SELECT id, author, body, created_at FROM lead_notes WHERE lead_key=%s ORDER BY created_at DESC",
            (lead_key,),
        ).fetchall()
        keys = ([lead["email"]] if lead["email"] else []) + [f"chat:{c}" for c in lead["chat_ids"]]
        placeholders = ",".join(["%s"] * len(keys))
        events = conn.execute(
            f"SELECT event_type, event_date, amount, created_at FROM crm_events WHERE lower(email) IN ({placeholders}) "
            "AND source='chatterfy' ORDER BY event_date ASC NULLS LAST, created_at ASC",
            tuple(keys),
        ).fetchall() if keys else []
    finally:
        conn.close()
    hide_money = user["role"] not in LEAD_MANAGER_ROLES
    matched = lead.pop("matched_accounts", [])
    lead["broker_accounts"] = [{
        "broker": a["broker"], "account_id": a["account_id"], "label": a.get("label"),
        "registration_date": a.get("registration_date"), "ftd_date": a.get("ftd_date"),
        "deposits": None if hide_money else a.get("deposits"),
        "balance": None if hide_money else a.get("balance"),
    } for a in matched]
    seen_types = {}
    for e in events:  # one entry per event type: the export and the webhook may both report REG
        t = str(e["event_type"] or "").upper()
        item = {"type": t, "date": e["event_date"] or e["created_at"], "amount": None if hide_money else e["amount"]}
        if t not in seen_types or (item["amount"] is not None and seen_types[t]["amount"] is None):
            seen_types[t] = item
    lead["events"] = sorted(seen_types.values(), key=lambda x: STAGE_ORDER.get(x["type"], 9))
    lead["note_list"] = [dict(n) for n in notes]
    return lead



# ---------------------------------------------------------------------------
# Reconciliation: Chatterfy leads/events vs broker reports
# ---------------------------------------------------------------------------

RECON_LIMIT = 50


def _lead_brief(lead):
    return {
        "lead_key": lead["lead_key"], "email": lead["email"], "name": lead.get("name"),
        "campaign": lead.get("campaign"), "source": lead.get("source"), "stage": lead["stage"],
        "chatterfy_ftd": lead.get("ftd_amount"), "match": lead.get("broker_match"),
        "brokers": lead.get("brokers", []),
    }


def _account_brief(a):
    return {
        "broker": a["broker"], "account_id": a["account_id"], "email": a.get("email"),
        "name": a.get("name"), "country": a.get("country"), "label": a.get("label"),
        "registration_date": a.get("registration_date"), "ftd_date": a.get("ftd_date"),
        "deposits": a.get("deposits"), "ftd_amount": a.get("ftd_amount"),
    }


def reconcile(leads, accounts):
    matched_ids = set()
    by_method = {"uid": 0, "label": 0, "email": 0, "phone": 0}
    ftd_no_deposit, deposit_no_ftd, amount_mismatch, not_found = [], [], [], []
    for lead in leads:
        accs = lead.get("matched_accounts") or []
        for a in accs:
            matched_ids.add((a["broker"], a["account_id"]))
        if lead.get("broker_match"):
            by_method[lead["broker_match"]] += 1
        has_deposit = any(broker_lib.account_has_deposit(a) for a in accs)
        stage_rank = STAGE_ORDER.get(lead["stage"], 0)
        if stage_rank >= STAGE_ORDER["FTD"] and not has_deposit:
            ftd_no_deposit.append({**_lead_brief(lead), "reason": "not_found" if not accs else "no_deposit"})
        if accs and has_deposit and stage_rank < STAGE_ORDER["FTD"]:
            deposit_no_ftd.append({**_lead_brief(lead), "broker_deposits": sum(float(a.get("deposits") or 0) for a in accs)})
        if stage_rank == STAGE_ORDER["REG"] and not accs:
            not_found.append(_lead_brief(lead))
        chat_amount = lead.get("ftd_amount")
        if chat_amount and accs:
            ftd_amounts = [float(a["ftd_amount"]) for a in accs if a.get("ftd_amount")]
            total_deposits = sum(float(a.get("deposits") or 0) for a in accs)
            broker_amount = ftd_amounts[0] if ftd_amounts else None
            bad = (abs(broker_amount - chat_amount) > max(1.0, 0.02 * chat_amount)) if broker_amount is not None \
                else (total_deposits and total_deposits + 1 < chat_amount)
            if bad:
                amount_mismatch.append({**_lead_brief(lead), "broker_ftd": broker_amount, "broker_deposits": total_deposits})

    # Group broker rows into clients (one person can have several accounts/rows).
    clients = {}
    for a in accounts:
        key = (a["broker"], broker_lib.normalize_email(a.get("email")) or a["account_id"])
        clients.setdefault(key, []).append(a)
    unattributed = []
    for key, accs in clients.items():
        if any((a["broker"], a["account_id"]) in matched_ids for a in accs):
            continue
        best = max(accs, key=lambda a: float(a.get("deposits") or 0))
        unattributed.append({**_account_brief(best), "accounts": len(accs),
                             "deposits": sum(float(a.get("deposits") or 0) for a in accs) or best.get("deposits")})
    unattributed.sort(key=lambda x: (float(x.get("deposits") or 0), x.get("registration_date") or ""), reverse=True)

    with_label = sum(1 for a in accounts if a.get("label"))
    return {
        "counts": {
            "leads": len(leads),
            "matched": sum(by_method.values()),
            "by_uid": by_method["uid"], "by_label": by_method["label"], "by_email": by_method["email"], "by_phone": by_method["phone"],
            "broker_clients": len(clients),
            "unattributed_clients": len(unattributed),
            "unattributed_with_deposit": sum(1 for x in unattributed if float(x.get("deposits") or 0) > 0),
            "ftd_no_deposit": len(ftd_no_deposit),
            "deposit_no_ftd": len(deposit_no_ftd),
            "amount_mismatch": len(amount_mismatch),
            "reg_not_found": len(not_found),
            "label_coverage": round(with_label / len(accounts) * 100, 1) if accounts else 0,
        },
        "ftd_no_deposit": ftd_no_deposit[:RECON_LIMIT],
        "deposit_no_ftd": deposit_no_ftd[:RECON_LIMIT],
        "amount_mismatch": amount_mismatch[:RECON_LIMIT],
        "reg_not_found": not_found[:RECON_LIMIT],
        "unattributed": unattributed[:RECON_LIMIT],
    }


@app.get("/api/v1/reconciliation")
def reconciliation(broker: str = "", x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        user = require_access(x_telegram_username, conn)
        if user["role"] not in LEAD_MANAGER_ROLES:
            raise HTTPException(status_code=403, detail="Reconciliation access required")
        all_accounts = load_broker_accounts(conn)
        brokers = sorted({a["broker"] for a in all_accounts} | {"FxPro"})
        accounts = [a for a in all_accounts if a["broker"] == broker] if broker else all_accounts
        leads = build_leads(conn, user, accounts=accounts)
        last_import = conn.execute(
            "SELECT broker, MAX(imported_at) AS at FROM broker_accounts GROUP BY broker"
        ).fetchall()
    finally:
        conn.close()
    result = reconcile(leads, accounts)
    result["brokers"] = brokers
    result["broker"] = broker
    result["last_import"] = {r["broker"]: r["at"] for r in last_import}
    return result


# ---------------------------------------------------------------------------
# Broker report -> Chatterfy. FxPro has no postbacks, so after a report upload
# the CRM finds leads that registered / deposited at the broker and sends the
# event to Chatterfy's Tracker "Custom Postback" (matched there by clickid).
# ---------------------------------------------------------------------------

CHATTERFY_POSTBACK_KEY = "chatterfy_postback_url"
CHATTERFY_PUSH_LIMIT = 200


def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM app_settings WHERE key=%s", (key,)).fetchone()
    return (row["value"] if row and row["value"] is not None else default)


def set_setting(conn, key, value, username):
    conn.execute(
        "INSERT INTO app_settings(key, value, updated_at, updated_by) VALUES(%s,%s,%s,%s) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, value, datetime.utcnow().isoformat(), username),
    )


def _postback_base(url):
    """Drop query params whose value is a template ({...}); we fill them ourselves."""
    import urllib.parse
    parts = urllib.parse.urlsplit(url.strip())
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if "{" not in v and "}" not in v]
    return parts, kept


def chatterfy_push_candidates(conn):
    """(lead, event, accounts) pairs the broker report proves but Chatterfy does not know yet."""
    leads = build_leads(conn, {"role": "admin", "username": "system"})
    done = {(r["lead_key"], r["event"]) for r in conn.execute(
        "SELECT lead_key, event FROM chatterfy_pushes WHERE status='sent'").fetchall()}
    out = []
    for x in leads:
        accs = x.get("matched_accounts") or []
        if not accs:
            continue
        events = ["REG"]
        if any(broker_lib.account_has_deposit(a) for a in accs):
            events.append("FTD")
        for ev in events:
            if STAGE_ORDER.get(x["stage"], 0) >= STAGE_ORDER[ev]:
                continue  # Chatterfy already has this stage
            if (x["lead_key"], ev) in done:
                continue
            out.append((x, ev, accs))
    return out


def _deposit_amount(accs):
    amounts = [float(a.get("ftd_amount") or 0) for a in accs if a.get("ftd_amount")]
    if amounts:
        return round(max(amounts), 2)
    total = sum(float(a.get("deposits") or 0) for a in accs)
    return round(total, 2) if total > 0 else None


def run_chatterfy_push(conn, dry_run=False):
    import urllib.parse
    import urllib.request
    url = get_setting(conn, CHATTERFY_POSTBACK_KEY)
    candidates = chatterfy_push_candidates(conn)
    stats = {"configured": bool(url), "candidates": len(candidates), "sent": 0, "failed": 0,
             "no_click_id": 0, "dry_run": dry_run, "examples": []}
    if not url:
        return stats
    parts, kept = _postback_base(url)
    for lead, ev, accs in candidates[:CHATTERFY_PUSH_LIMIT]:
        click_id = (lead.get("click_id") or "").strip()
        if not click_id:
            stats["no_click_id"] += 1
            continue
        amount = _deposit_amount(accs) if ev == "FTD" else None
        reg_dates = [a.get("registration_date") for a in accs if a.get("registration_date")]
        params = kept + [
            ("clickid", click_id),
            ("tracker.event", "registration" if ev == "REG" else "sale"),
            ("tracker.tid", f"{lead['lead_key']}:{ev}"),
            ("fields.broker_event", ev),
            ("fields.broker_id", str(accs[0].get("account_id") or "")),
            ("fields.datereg", (min(reg_dates) if reg_dates else "")[:10]),
        ]
        if amount is not None:
            params += [("tracker.cost", str(amount)), ("tracker.currency", "USD"), ("fields.deposit_amount", str(amount))]
        full = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(params), ""))
        if len(stats["examples"]) < 3:
            stats["examples"].append({"lead": lead.get("name") or lead.get("email") or lead["lead_key"], "event": ev, "amount": amount})
        if dry_run:
            continue
        status, response = "sent", ""
        try:
            with urllib.request.urlopen(full, timeout=10) as r:
                response = f"HTTP {r.status}"
        except Exception as exc:  # keep going: one bad call must not stop the rest
            status, response = "failed", f"{type(exc).__name__}: {exc}"[:300]
        stats["sent" if status == "sent" else "failed"] += 1
        conn.execute(
            "INSERT INTO chatterfy_pushes(lead_key, event, click_id, amount, status, response, sent_at) VALUES(%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(lead_key, event) DO UPDATE SET status=excluded.status, response=excluded.response, sent_at=excluded.sent_at, amount=excluded.amount",
            (lead["lead_key"], ev, click_id, amount, status, response, datetime.utcnow().isoformat()),
        )
    if not dry_run:
        conn.commit()
    return stats


def push_after_import():
    """Called after a broker report upload; never fails the upload itself."""
    try:
        conn = db()
        try:
            return run_chatterfy_push(conn)
        finally:
            conn.close()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}


class ChatterfyPostbackSetting(BaseModel):
    url: str = ""


@app.get("/api/v1/settings/chatterfy-postback")
def read_chatterfy_postback(x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        require_admin(x_telegram_username, conn)
        url = get_setting(conn, CHATTERFY_POSTBACK_KEY)
        last = conn.execute("SELECT status, COUNT(*) AS c FROM chatterfy_pushes GROUP BY status").fetchall()
        return {"url": url, "pushes": {r["status"]: int(r["c"]) for r in last}}
    finally:
        conn.close()


@app.post("/api/v1/settings/chatterfy-postback")
def save_chatterfy_postback(payload: ChatterfyPostbackSetting, x_telegram_username: str = Depends(current_username)):
    url = (payload.url or "").strip()
    if url and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Ссылка должна начинаться с https://")
    conn = db()
    try:
        username = require_admin(x_telegram_username, conn)
        set_setting(conn, CHATTERFY_POSTBACK_KEY, url, username)
        conn.commit()
        return {"status": "ok", "url": url}
    finally:
        conn.close()


@app.post("/api/v1/chatterfy/push")
def push_to_chatterfy(dry_run: bool = False, x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        require_admin(x_telegram_username, conn)
        return run_chatterfy_push(conn, dry_run=dry_run)
    finally:
        conn.close()


class LeadWorkUpdate(BaseModel):
    work_status: str | None = None
    assignee: str | None = None  # "" unassigns
    callback_at: str | None = None  # ISO UTC; "" clears


@app.post("/api/v1/leads/{lead_key:path}/work")
def update_lead_work(lead_key: str, payload: LeadWorkUpdate, x_telegram_username: str = Depends(current_username)):
    conn = db()
    try:
        user = require_lead_access(x_telegram_username, conn)
        me = user["username"].lower()
        row = conn.execute("SELECT * FROM lead_work WHERE lead_key=%s", (lead_key,)).fetchone()
        current = dict(row) if row else {"assignee": None, "work_status": "new"}
        status = current["work_status"]
        assignee = current["assignee"]

        if payload.work_status is not None:
            if payload.work_status not in WORK_STATUSES:
                raise HTTPException(status_code=400, detail="Invalid work status")
            if user["role"] not in LEAD_MANAGER_ROLES and assignee and assignee.lower() != me:
                raise HTTPException(status_code=403, detail=f"Lead is assigned to @{assignee}")
            status = payload.work_status
            # Handlers who start working on an unassigned lead take it.
            if not assignee and user["role"] == "handler":
                assignee = me

        if payload.assignee is not None:
            target = payload.assignee.lstrip("@").strip().lower() or None
            if user["role"] not in LEAD_MANAGER_ROLES:
                # Handlers may only take a free lead or release their own.
                allowed = (target == me and not assignee) or (target is None and (assignee or "").lower() == me)
                if not allowed:
                    raise HTTPException(status_code=403, detail="Only admins can reassign leads")
            elif target:
                exists = conn.execute("SELECT 1 FROM crm_users WHERE lower(username)=%s AND active=TRUE", (target,)).fetchone()
                if not exists:
                    raise HTTPException(status_code=400, detail=f"@{target} is not an active CRM user")
            assignee = target

        callback_at = current.get("callback_at")
        if payload.callback_at is not None:
            if assignee and user["role"] not in LEAD_MANAGER_ROLES and assignee.lower() != me:
                raise HTTPException(status_code=403, detail=f"Lead is assigned to @{assignee}")
            raw = payload.callback_at.strip()
            if raw:
                try:
                    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid callback time")
                if parsed.tzinfo:
                    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                callback_at = parsed.isoformat(timespec="seconds")
                status = "callback"
                if not assignee and user["role"] == "handler":
                    assignee = me
            else:
                callback_at = None
        if status != "callback":
            callback_at = None

        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO lead_work(lead_key, assignee, work_status, updated_at, updated_by, callback_at) VALUES(%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(lead_key) DO UPDATE SET assignee=excluded.assignee, work_status=excluded.work_status, "
            "updated_at=excluded.updated_at, updated_by=excluded.updated_by, callback_at=excluded.callback_at",
            (lead_key, assignee, status, now, me, callback_at),
        )
        conn.commit()
        if assignee and assignee.lower() != me and (current.get("assignee") or "").lower() != assignee.lower():
            lead = next((x for x in build_leads(conn, {"role": "admin", "username": "system"}) if x["lead_key"] == lead_key), None)
            notify_user(conn, assignee, f"📌 @{me} передал тебе лид: {lead_label(lead or {'lead_key': lead_key})}\nОткрой мини-апп → «Мой день».")
        return {"status": "ok", "lead_key": lead_key, "assignee": assignee, "work_status": status, "callback_at": callback_at}
    finally:
        conn.close()


class LeadNote(BaseModel):
    body: str


@app.post("/api/v1/leads/{lead_key:path}/notes")
def add_lead_note(lead_key: str, payload: LeadNote, x_telegram_username: str = Depends(current_username)):
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Note is empty")
    if len(body) > 2000:
        raise HTTPException(status_code=400, detail="Note is too long (max 2000 characters)")
    conn = db()
    try:
        user = require_lead_access(x_telegram_username, conn)
        note = {"id": uuid.uuid4().hex, "author": user["username"], "body": body, "created_at": datetime.utcnow().isoformat()}
        conn.execute(
            "INSERT INTO lead_notes(id, lead_key, author, body, created_at) VALUES(%s,%s,%s,%s,%s)",
            (note["id"], lead_key, note["author"], note["body"], note["created_at"]),
        )
        conn.commit()
        return note
    finally:
        conn.close()


@app.get("/api/v1/report/handlers")
def handlers_report(days: int = 7, x_telegram_username: str = Depends(current_username)):
    """Head view: lead -> REG -> FTD funnel per handler for leads that came in during the period."""
    conn = db()
    try:
        user = require_access(x_telegram_username, conn)
        if user["role"] not in LEAD_MANAGER_ROLES:
            raise HTTPException(status_code=403, detail="Report access required")
        leads = build_leads(conn, user)
        team = [r["username"] for r in conn.execute(
            "SELECT username FROM crm_users WHERE active=TRUE AND role='handler' ORDER BY username").fetchall()]
    finally:
        conn.close()
    now = datetime.utcnow()
    cutoff = (now - timedelta(days=days)).isoformat() if days and days > 0 else ""
    stale_before = (now - timedelta(hours=48)).isoformat()
    now_iso = now.isoformat()

    def blank(name):
        return {"handler": name, "leads": 0, "reg": 0, "ftd": 0, "deposits": 0.0,
                "in_work": 0, "callbacks_overdue": 0, "stale": 0, "lost": 0}

    rows = {name: blank(name) for name in team}
    for x in leads:
        if cutoff and (x["first_seen_at"] or x["last_activity"] or "") < cutoff:
            continue
        name = (x["assignee"] or "").lower() or None
        r = rows.setdefault(name, blank(name))
        accs = x.get("matched_accounts") or []
        rank = STAGE_ORDER.get(x["stage"], 0)
        has_dep = any(broker_lib.account_has_deposit(a) for a in accs)
        is_reg = rank >= STAGE_ORDER["REG"] or bool(accs)
        is_ftd = rank >= STAGE_ORDER["FTD"] or has_dep
        r["leads"] += 1
        r["reg"] += int(is_reg)
        r["ftd"] += int(is_ftd)
        dep = sum(float(a.get("deposits") or 0) for a in accs)
        r["deposits"] += dep or float(x.get("ftd_amount") or 0)
        if x["work_status"] == "lost":
            r["lost"] += 1
        elif not is_ftd and x["work_status"] != "won":
            r["in_work"] += 1
            if x["work_status"] == "callback" and x.get("callback_at") and x["callback_at"] < now_iso:
                r["callbacks_overdue"] += 1
            if (x["last_activity"] or "") < stale_before:
                r["stale"] += 1

    out = []
    for r in rows.values():
        r["deposits"] = round(r["deposits"], 2)
        r["lead_to_reg"] = round(r["reg"] / r["leads"] * 100, 1) if r["leads"] else 0
        r["reg_to_ftd"] = round(r["ftd"] / r["reg"] * 100, 1) if r["reg"] else 0
        r["lead_to_ftd"] = round(r["ftd"] / r["leads"] * 100, 1) if r["leads"] else 0
        out.append(r)
    handlers = sorted([r for r in out if r["handler"]], key=lambda r: (-r["ftd"], -r["reg"], r["handler"]))
    unassigned = next((r for r in out if not r["handler"]), blank(None))
    total = blank("total")
    for r in out:
        for k in ("leads", "reg", "ftd", "deposits", "in_work", "callbacks_overdue", "stale", "lost"):
            total[k] += r[k]
    total["deposits"] = round(total["deposits"], 2)
    total["lead_to_reg"] = round(total["reg"] / total["leads"] * 100, 1) if total["leads"] else 0
    total["reg_to_ftd"] = round(total["ftd"] / total["reg"] * 100, 1) if total["reg"] else 0
    total["lead_to_ftd"] = round(total["ftd"] / total["leads"] * 100, 1) if total["leads"] else 0
    return {"days": days, "handlers": handlers, "unassigned": unassigned, "total": total}


# Serve the built Telegram Mini App from the same HTTPS origin as the API.
# API routes are registered above, so this catch-all only handles frontend assets/pages.
if os.path.isdir("/app/web/dist"):
    app.mount("/", StaticFiles(directory="/app/web/dist", html=True), name="web")