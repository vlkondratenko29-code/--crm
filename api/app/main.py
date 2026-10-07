from datetime import datetime
import csv
import os
import io

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import is_admin

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("DB_PATH", "broker_crm.db")
CHATTERFY_WEBHOOK_URL = os.getenv("CHATTERFY_WEBHOOK_URL", "")

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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS broker_clients (
            email TEXT PRIMARY KEY,
            broker_id TEXT,
            status TEXT,
            country TEXT,
            click_id TEXT,
            registration_date TEXT,
            first_fund_date TEXT,
            first_fund_amount REAL,
            first_trade_date TEXT,
            last_trade_date TEXT,
            net_deposits REAL,
            deposits REAL,
            latest_balance REAL,
            trading_volume REAL
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
            usd REAL,
            deposits REAL,
            withdrawals REAL,
            latest_balance REAL,
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crm_users (
            username TEXT PRIMARY KEY,
            role TEXT NOT NULL DEFAULT 'handler',
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crm_events (
            event_key TEXT PRIMARY KEY,
            email TEXT,
            event_type TEXT NOT NULL,
            event_date TEXT,
            amount REAL,
            source TEXT NOT NULL,
            broker_id TEXT,
            fxpro_login TEXT,
            chat_id TEXT,
            metadata_json TEXT,
            created_at TEXT
        )
    """)
    for admin_username in ("jokwq", "nodari777"):
        conn.execute(
            "INSERT OR IGNORE INTO crm_users(username, role, active) VALUES(?,?,1)",
            (admin_username, "admin"),
        )
    conn.commit()
    return conn

app = FastAPI(title="Broker CRM API", version="0.5.0")

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
        try:
            conn.execute("ALTER TABLE fxpro_accounts ADD COLUMN IF NOT EXISTS email TEXT")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chatterfy_leads (
                chat_id TEXT PRIMARY KEY,
                email TEXT,
                click_id TEXT,
                last_synced_event TEXT
            )
        """)
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
        try:
            conn.execute("ALTER TABLE chatterfy_leads ADD COLUMN IF NOT EXISTS attribution_json TEXT")
        except Exception:
            pass
        for admin_username in ("jokwq", "nodari777"):
            conn.execute(
                "INSERT INTO crm_users(username, role, active) VALUES(%s,%s,TRUE) ON CONFLICT(username) DO UPDATE SET role='admin', active=TRUE",
                (admin_username, "admin"),
            )
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
        normalized = {str(k).strip().lower().replace("\ufeff", "").replace('"', ""): v for k, v in row.items() if k is not None}
        for name in names:
            v = normalized.get(name.lower())
            if v not in (None, ""):
                return v
        return None

    accounts = []
    for row in rows:
        login = str(value(row, "Логин", "Login", "Account", "Account ID") or "").strip()
        if not login:
            continue
        email = (value(row, "Email", "Email Address", "EmailAddress", "email") or "").strip().lower()
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
            "last_trade_date": parse_date(value(row, "Последняя сделка", "Last Trade", "Last Trade Date")),
        })
    return accounts

def record_event(conn, *, email, event_type, event_date=None, amount=None, source="unknown",
                 broker_id=None, fxpro_login=None, chat_id=None, metadata=None):
    """Persist a deterministic client event without creating duplicates."""
    if not email or not event_type:
        return
    import hashlib, json
    email_key = email.strip().lower()
    raw_key = "|".join([
        email_key, str(event_type).upper(), str(event_date or ""),
        str(source or ""), str(fxpro_login or ""), str(broker_id or ""),
        str(chat_id or "")
    ])
    event_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    now = datetime.utcnow().isoformat()
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


class TeamUser(BaseModel):
    username: str
    role: str
    active: bool = True


@app.get("/api/v1/users")
def list_users(x_telegram_username: str = Header(default="")):
    conn = db()
    try:
        require_admin(x_telegram_username, conn)
        rows = conn.execute("SELECT username, role, active FROM crm_users ORDER BY username").fetchall()
        return {"users": [dict(row) for row in rows]}
    finally:
        conn.close()


@app.post("/api/v1/users")
def upsert_user(payload: TeamUser, x_telegram_username: str = Header(default="")):
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
def deactivate_user(username: str, x_telegram_username: str = Header(default="")):
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
def me(x_telegram_username: str = Header(default="")):
    user = require_access(x_telegram_username)
    return {"username": user["username"], "role": user["role"], "is_admin": user["role"] == "admin"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "broker-crm-api"}


def clean_attribution_value(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value or "{{" in value or "}}" in value:
        return None
    return value

def attribution_text(attr, *keys):
    for key in keys:
        value = clean_attribution_value(attr.get(key))
        if value:
            return value
    return None


@app.api_route("/webhook/chatterfy", methods=["GET", "POST"])
async def chatterfy_webhook(
    request: Request,
    email: str | None = None, chat_id: str | None = None, click_id: str | None = None,
    broker_id: str | None = None, broker_event: str | None = None, deposit_amount: str | None = None,
    datereg: str | None = None,
    ad_id: str | None = None, site_source_name: str | None = None, utm_term: str | None = None,
    tracker_campaign_type: str | None = None, utm_id: str | None = None, utm_medium: str | None = None,
    utm_source: str | None = None, utm_campaign: str | None = None, campaign_name: str | None = None,
    ad_campaign_id: str | None = None, adset_id: str | None = None, placement: str | None = None,
    adset_name: str | None = None, utm_content: str | None = None, tracker_provider_type: str | None = None,
    tracker_campaign: str | None = None, tracker_source: str | None = None, tracker_domain_id: str | None = None,
    tracker_landing_id: str | None = None, tracker_source_name: str | None = None,
    tracker_campaign_name: str | None = None
):
    attribution = {
        "ad_id": ad_id, "site_source_name": site_source_name, "utm_term": utm_term,
        "tracker_campaign_type": tracker_campaign_type, "utm_id": utm_id, "utm_medium": utm_medium,
        "utm_source": utm_source, "utm_campaign": utm_campaign, "campaign_name": campaign_name,
        "ad_campaign_id": ad_campaign_id, "adset_id": adset_id, "placement": placement,
        "adset_name": adset_name, "utm_content": utm_content, "tracker_provider_type": tracker_provider_type,
        "tracker_campaign": tracker_campaign, "tracker_source": tracker_source,
        "tracker_domain_id": tracker_domain_id, "tracker_landing_id": tracker_landing_id,
        "tracker_source_name": tracker_source_name, "tracker_campaign_name": tracker_campaign_name
    }
    if request.method == "POST":
        try:
            payload = await request.json()
            email = email or payload.get("email")
            chat_id = chat_id or payload.get("chat_id") or payload.get("chatId")
            click_id = click_id or payload.get("click_id") or payload.get("clickId")
            broker_id = broker_id or payload.get("broker_id") or payload.get("brokerId")
            broker_event = broker_event or payload.get("broker_event") or payload.get("brokerEvent")
            deposit_amount = deposit_amount or payload.get("deposit_amount") or payload.get("depositAmount")
            datereg = datereg or payload.get("datereg") or payload.get("dateReg")
            for key in attribution:
                attribution[key] = attribution[key] or payload.get(key)
        except Exception:
            pass
    if not email or not chat_id:
        raise HTTPException(status_code=400, detail="email and chat_id are required")
    import json
    attribution = {k: clean_attribution_value(v) for k, v in attribution.items()}
    attribution_json = json.dumps({k:v for k,v in attribution.items() if v is not None}, ensure_ascii=False)
    conn = db()
    conn.execute("INSERT INTO chatterfy_leads(chat_id,email,click_id,attribution_json) VALUES(%s,%s,%s,%s) ON CONFLICT(chat_id) DO UPDATE SET email=excluded.email, click_id=excluded.click_id, attribution_json=excluded.attribution_json", (chat_id, email.strip().lower(), click_id, attribution_json))
    if broker_event:
        amount = to_float(deposit_amount)
        record_event(
            conn, email=email, event_type=broker_event, event_date=datereg,
            amount=amount, source="chatterfy", broker_id=broker_id, chat_id=chat_id,
            metadata={"click_id": click_id}
        )
    conn.commit()
    conn.close()
    client = find_client(email, click_id)
    if not client:
        return {"status": "pending", "matched": False, "email": email, "chat_id": chat_id}
    sync = sync_client_to_chatterfy(chat_id, client)
    return {"status": "ok", "matched": True, "email": email, "chat_id": chat_id, "sync": sync}


@app.post("/api/v1/broker/fxpro/import")
async def import_fxpro_report(files: list[UploadFile] = File(...), x_telegram_username: str = Header(default="")):
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
        (login,email,name,country,jurisdiction,ib_group,registration_date,active,currency,usd,deposits,withdrawals,latest_balance,last_trade_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(login) DO UPDATE SET email=excluded.email,name=excluded.name,country=excluded.country,jurisdiction=excluded.jurisdiction,ib_group=excluded.ib_group,registration_date=excluded.registration_date,active=excluded.active,currency=excluded.currency,usd=excluded.usd,deposits=excluded.deposits,withdrawals=excluded.withdrawals,latest_balance=excluded.latest_balance,last_trade_date=excluded.last_trade_date"""
        keys = ("email","broker_id","status","country","click_id","registration_date","first_fund_date","first_fund_amount","first_trade_date","last_trade_date","net_deposits","deposits","latest_balance","trading_volume")

        imported_clients = 0
        imported_accounts = 0
        files_ok = 0
        file_types = []
        for file in files:
            raw = await file.read()
            accounts = parse_fxpro_clients_report(raw)
            if accounts:
                for account in accounts:
                    conn.execute(account_sql, tuple(account.get(k) for k in ("login","email","name","country","jurisdiction","ib_group","registration_date","active","currency","usd","deposits","withdrawals","latest_balance","last_trade_date")))
                    record_fxpro_account_events(conn, account)
                imported_accounts += len(accounts)
                file_types.append("clients")
                files_ok += 1
                continue

            clients = parse_fxpro_report(raw)
            if clients:
                for client in clients:
                    conn.execute(sql, tuple(client.get(k) for k in keys))
                    record_fxpro_client_events(conn, client)
                imported_clients += len(clients)
                file_types.append("detailed")
                files_ok += 1

        conn.commit()

        pending = conn.execute("SELECT chat_id,email,click_id FROM chatterfy_leads").fetchall()
        synced = 0
        for lead in pending:
            client = find_client(lead["email"], lead["click_id"])
            if client:
                try:
                    sync_client_to_chatterfy(lead["chat_id"], client)
                    event = "FT" if client.get("first_trade_date") else ("FTD" if client.get("first_fund_date") else "REG")
                    conn.execute("UPDATE chatterfy_leads SET last_synced_event=%s WHERE chat_id=%s", (event, lead["chat_id"]))
                    synced += 1
                except Exception:
                    pass
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
        return {"status":"ok","broker":"FxPro","files":files_ok,"client_rows":imported_clients,"account_rows":imported_accounts,"rows":imported_clients + imported_accounts,"chatterfy_synced":synced,"email_linked_clients":email_linked_clients,"file_types":file_types}
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
def search_clients(q: str, x_telegram_username: str = Header(default="")):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    conn = db()
    term = "%" + q.strip().lower() + "%"

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
        SELECT chat_id, email, click_id, attribution_json
        FROM chatterfy_leads
        WHERE lower(coalesce(email,'')) LIKE %s
           OR lower(coalesce(click_id,'')) LIKE %s
        ORDER BY chat_id DESC
        LIMIT 100
    """, (term, term)).fetchall()

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

    conn.close()

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
        item["fxpro_account_count"] = len(item["fxpro_accounts"])
        if user["role"] == "seo":
            for key in ("first_fund_amount", "net_deposits", "deposits", "latest_balance", "trading_volume"):
                item[key] = None
            for account in item["fxpro_accounts"]:
                for key in ("deposits", "withdrawals", "latest_balance", "usd"):
                    account[key] = None
        item["events"] = events_for_email((item.get("email") or "").strip().lower())
        if not item["events"] and item.get("registration_date"):
            item["events"].append({"type":"REG","date":item["registration_date"],"source":"fxpro"})
        if item.get("first_fund_date") and not any(e.get("type") == "FTD" for e in item["events"]):
            item["events"].append({"type":"FTD","date":item["first_fund_date"],"amount":item.get("first_fund_amount"),"source":"fxpro"})
        if item.get("first_trade_date") and not any(e.get("type") == "FT" for e in item["events"]):
            item["events"].append({"type":"FT","date":item["first_trade_date"],"source":"fxpro"})
        result.append(item)

    # If a lead exists in Chatterfy but is not present in either FxPro
    # report, synthesize a lead-only client card. When FxPro appears later,
    # the same Email will naturally resolve to the broker-backed card above.
    for lead in chatterfy_rows:
        email_key = (lead["email"] or "").strip().lower()
        if not email_key or email_key in seen_emails:
            continue
        try:
            attr = json.loads(lead["attribution_json"]) if lead["attribution_json"] else {}
        except Exception:
            attr = {}
        attr = {k: clean_attribution_value(v) for k, v in attr.items()}
        attr = {k: v for k, v in attr.items() if v is not None}
        item = {
            "email": email_key,
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
            "events": [],
        }
        result.append(item)
        seen_emails.add(email_key)

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
        if item["registration_date"] and not any(e.get("type") == "REG" for e in item["events"]):
            item["events"].append({"type":"REG","date":item["registration_date"],"source":"fxpro"})
        if item["first_trade_date"] and not any(e.get("type") == "FT" for e in item["events"]):
            item["events"].append({"type":"FT","date":item["first_trade_date"],"source":"fxpro"})
        if deposits > 0 and not any(e.get("type") in ("FTD","RD") for e in item["events"]):
            item["events"].append({"type":"FUNDED","amount":deposits,"source":"fxpro_account_summary"})
        if user["role"] == "seo":
            for key in ("first_fund_amount", "net_deposits", "deposits", "latest_balance", "trading_volume"):
                item[key] = None
            for account in item["fxpro_accounts"]:
                for key in ("deposits", "withdrawals", "latest_balance", "usd"):
                    account[key] = None
        result.append(item)

    return {"clients": result[:20]}


@app.get("/api/v1/traffic")
def traffic(x_telegram_username: str = Header(default="")):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    conn = db()
    try:
        rows = conn.execute("""
            SELECT b.email, b.registration_date, b.first_fund_date, b.first_trade_date,
                   b.first_fund_amount, l.click_id AS chatterfy_click_id, l.attribution_json
            FROM broker_clients b
            LEFT JOIN chatterfy_leads l ON lower(l.email) = lower(b.email)
        """).fetchall()
    finally:
        conn.close()

    import json
    best = {}
    for r in rows:
        try:
            raw = json.loads(r["attribution_json"]) if r["attribution_json"] else {}
        except Exception:
            raw = {}
        attr = {k: clean_attribution_value(v) for k, v in raw.items()}
        attr = {k: v for k, v in attr.items() if v is not None}
        if not attr:
            continue
        score = sum(1 for k in ("tracker_campaign", "tracker_source", "tracker_campaign_type",
                                "tracker_provider_type", "tracker_domain_id", "tracker_landing_id",
                                "click_id", "ad_id", "adset_id", "placement") if attr.get(k))
        email = (r["email"] or "").strip().lower()
        if email and (email not in best or score > best[email]["score"]):
            best[email] = {"row": r, "attr": attr, "score": score}

    grouped = {}
    for item in best.values():
        r, attr = item["row"], item["attr"]
        campaign = attribution_text(attr, "tracker_campaign_name", "tracker_campaign") or "Unknown campaign"
        source = attribution_text(attr, "tracker_source_name", "tracker_source") or "Unknown source"
        adset = attribution_text(attr, "adset_name", "adset_id") or "Unknown adset"
        ad = attribution_text(attr, "ad_id") or "Unknown ad"
        placement = attribution_text(attr, "placement") or "Unknown placement"
        click = attribution_text(attr, "clickid") or r["chatterfy_click_id"] or ""
        click = str(click).strip() or "No Click ID"
        key = (campaign, source, adset, ad, placement)
        g = grouped.setdefault(key, {
            "campaign": campaign, "source": source, "adset": adset, "ad": ad,
            "placement": placement, "leads": 0, "reg": 0, "ftd": 0, "ft": 0, "deposits": 0.0,
            "clicks": set()
        })
        g["leads"] += 1
        g["reg"] += 1 if r["registration_date"] else 0
        g["ftd"] += 1 if r["first_fund_date"] else 0
        g["ft"] += 1 if r["first_trade_date"] else 0
        g["deposits"] += float(r["first_fund_amount"] or 0)
        g["clicks"].add(click)

    result = []
    for g in grouped.values():
        g["clicks"] = len(g["clicks"])
        g["reg_to_ftd"] = round(g["ftd"] / g["reg"] * 100, 1) if g["reg"] else 0
        g["ftd_to_ft"] = round(g["ft"] / g["ftd"] * 100, 1) if g["ftd"] else 0
        result.append(g)
    result.sort(key=lambda x: (x["ftd"], x["deposits"], x["leads"]), reverse=True)

    return {
        "rows": result,
        "total": len(result),
        "viewer": user["role"],
    }

@app.get("/api/v1/finance")
def finance(x_telegram_username: str = Header(default="")):
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
        clients = conn.execute("""
            SELECT country, first_fund_date, first_fund_amount, net_deposits
            FROM broker_clients
        """).fetchall()
    finally:
        conn.close()

    account_deposits = sum(float(r["deposits"] or 0) for r in accounts)
    withdrawals = sum(float(r["withdrawals"] or 0) for r in accounts)
    balance = sum(float(r["latest_balance"] or 0) for r in accounts)
    account_net = account_deposits - withdrawals

    ftd_deposits = sum(float(r["first_fund_amount"] or 0) for r in clients if r["first_fund_date"])
    ftd_count = sum(1 for r in clients if r["first_fund_date"])

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

@app.get("/api/v1/dashboard")
def dashboard(x_telegram_username: str = Header(default="")):
    username = x_telegram_username.lstrip("@").strip()
    user = require_access(username)
    conn = db()
    rows = conn.execute("SELECT * FROM broker_clients ORDER BY registration_date DESC NULLS LAST").fetchall()
    conn.close()

    total = len(rows)
    reg = sum(1 for r in rows if r["registration_date"])
    ftd = sum(1 for r in rows if r["first_fund_date"])
    ft = sum(1 for r in rows if r["first_trade_date"])
    deposits = sum(float(r["first_fund_amount"] or 0) for r in rows)

    countries = {}
    click_stats = {}
    attribution_stats = {}
    for r in rows:
        country = (r["country"] or "Unknown").strip() or "Unknown"
        countries[country] = countries.get(country, 0) + 1
        click_id = (r["click_id"] or "").strip() or "No Click ID"
        bucket = click_stats.setdefault(click_id, {"click_id": click_id, "leads": 0, "reg": 0, "ftd": 0, "ft": 0, "deposits": 0.0})
        bucket["leads"] += 1
        bucket["reg"] += 1 if r["registration_date"] else 0
        bucket["ftd"] += 1 if r["first_fund_date"] else 0
        bucket["ft"] += 1 if r["first_trade_date"] else 0
        bucket["deposits"] += float(r["first_fund_amount"] or 0)

    # Chatterfy is the attribution source. Join its latest stored attribution
    # to broker clients by email so Buying can see Campaign → Source → AdSet → Ad.
    lead_rows = None
    attr_conn = db()
    try:
        lead_rows = attr_conn.execute("""
            SELECT b.email, b.click_id, b.registration_date, b.first_fund_date, b.first_trade_date,
                   b.first_fund_amount, l.click_id AS chatterfy_click_id, l.attribution_json
            FROM broker_clients b
            LEFT JOIN chatterfy_leads l ON lower(l.email) = lower(b.email)
        """).fetchall()
    finally:
        attr_conn.close()

    import json

    # Pick the best Chatterfy attribution per FxPro email. Test conversations can
    # leave multiple webhook rows for the same client; ignore placeholders/empty
    # attribution and prefer the row with the richest real tracker data.
    best_by_email = {}
    for r in lead_rows:
        try:
            raw_attr = json.loads(r["attribution_json"]) if r["attribution_json"] else {}
        except Exception:
            raw_attr = {}
        attr = {k: clean_attribution_value(v) for k, v in raw_attr.items()}
        attr = {k: v for k, v in attr.items() if v is not None}
        if not attr:
            continue
        score = sum(1 for k in (
            "tracker_campaign", "tracker_source", "tracker_campaign_type",
            "tracker_provider_type", "tracker_domain_id", "tracker_landing_id",
            "click_id", "ad_id", "adset_id", "placement"
        ) if attr.get(k))
        email_key = (r["email"] or "").strip().lower()
        current = best_by_email.get(email_key)
        if current is None or score > current["score"]:
            best_by_email[email_key] = {"row": r, "attr": attr, "score": score}

    for entry in best_by_email.values():
        r = entry["row"]
        attr = entry["attr"]
        campaign = attribution_text(attr, "tracker_campaign_name", "tracker_campaign")
        source = attribution_text(attr, "tracker_source_name", "tracker_source")
        adset = attribution_text(attr, "adset_name", "adset_id")
        ad = attribution_text(attr, "ad_id")
        placement = attribution_text(attr, "placement")
        click = attribution_text(attr, "clickid") or r["chatterfy_click_id"] or r["click_id"] or ""
        click = str(click).strip()
        # Only put real Chatterfy attribution into Buying analytics.
        if not campaign and not source and not click:
            continue
        campaign = campaign or "Unknown campaign"
        source = source or "Unknown source"
        adset = adset or "Unknown adset"
        ad = ad or "Unknown ad"
        placement = placement or "Unknown placement"
        click = click or "No Click ID"
        key = (campaign, source, adset, ad, placement, click)
        bucket = attribution_stats.setdefault(key, {
            "campaign": campaign, "source": source, "adset": adset, "ad": ad,
            "placement": placement, "click_id": click, "leads": 0, "reg": 0,
            "ftd": 0, "ft": 0, "deposits": 0.0
        })
        bucket["leads"] += 1
        bucket["reg"] += 1 if r["registration_date"] else 0
        bucket["ftd"] += 1 if r["first_fund_date"] else 0
        bucket["ft"] += 1 if r["first_trade_date"] else 0
        bucket["deposits"] += float(r["first_fund_amount"] or 0)

    geo_stats = {}
    daily_stats = {}
    for r in rows:
        geo = (r["country"] or "Unknown").strip() or "Unknown"
        g = geo_stats.setdefault(geo, {"country": geo, "leads": 0, "reg": 0, "ftd": 0, "ft": 0, "deposits": 0.0})
        g["leads"] += 1
        g["reg"] += 1 if r["registration_date"] else 0
        g["ftd"] += 1 if r["first_fund_date"] else 0
        g["ft"] += 1 if r["first_trade_date"] else 0
        g["deposits"] += float(r["first_fund_amount"] or 0)
        for date_value, key in ((r["registration_date"], "reg"), (r["first_fund_date"], "ftd"), (r["first_trade_date"], "ft")):
            if date_value:
                d = daily_stats.setdefault(date_value, {"date": date_value, "reg": 0, "ftd": 0, "ft": 0, "deposits": 0.0})
                d[key] += 1
                if key == "ftd":
                    d["deposits"] += float(r["first_fund_amount"] or 0)

    top_countries = sorted(countries.items(), key=lambda x: x[1], reverse=True)[:5]
    top_geo = sorted(geo_stats.values(), key=lambda x: (x["ftd"], x["deposits"], x["reg"]), reverse=True)[:12]
    top_clicks = sorted(click_stats.values(), key=lambda x: (x["ftd"], x["leads"]), reverse=True)[:8]
    top_attribution = sorted(attribution_stats.values(), key=lambda x: (x["ftd"], x["reg"], x["leads"]), reverse=True)[:12]
    for item in top_clicks:
        item["reg_to_ftd"] = round(item["ftd"] / item["reg"] * 100, 1) if item["reg"] else 0
        item["ftd_to_ft"] = round(item["ft"] / item["ftd"] * 100, 1) if item["ftd"] else 0
    for item in top_attribution:
        item["reg_to_ftd"] = round(item["ftd"] / item["reg"] * 100, 1) if item["reg"] else 0
        item["ftd_to_ft"] = round(item["ft"] / item["ftd"] * 100, 1) if item["ftd"] else 0

    recent = []
    for r in rows[:5]:
        recent.append({
            "email": r["email"],
            "country": r["country"],
            "status": r["status"],
            "registration_date": r["registration_date"],
            "first_fund_date": r["first_fund_date"],
            "first_trade_date": r["first_trade_date"],
            "first_fund_amount": None if user["role"] == "seo" else r["first_fund_amount"],
            "click_id": r["click_id"],
        })

    try:
        ops_conn = db()
        fxpro_accounts_count = ops_conn.execute("SELECT COUNT(*) AS c FROM fxpro_accounts").fetchone()["c"]
        ops_conn.close()
    except Exception:
        fxpro_accounts_count = 0

    return {
        "leads": total,
        "reg": reg,
        "ftd": ftd,
        "ft": ft,
        "deposits": deposits if user["role"] != "seo" else None,
        "viewer": {"username": user["username"], "role": user["role"]},
        "funnel": {
            "reg_to_ftd": round(ftd / reg * 100, 1) if reg else 0,
            "ftd_to_ft": round(ft / ftd * 100, 1) if ftd else 0,
            "reg_to_ft": round(ft / reg * 100, 1) if reg else 0,
        },
        "top_countries": [{"country": k, "count": v} for k, v in top_countries],
        "geo": top_geo,
        "daily": sorted(daily_stats.values(), key=lambda x: x["date"])[-30:],
        "company": [{
            "name": "FxPro",
            "clients": total,
            "reg": reg,
            "ftd": ftd,
            "ft": ft,
            "deposits": deposits if user["role"] != "seo" else None,
            "net_deposits": sum(float(r["net_deposits"] or 0) for r in rows) if user["role"] != "seo" else None,
        }],
        "operations": {
            "clients": total,
            "attributed_clients": len(best_by_email),
            "unattributed_clients": max(total - len(best_by_email), 0),
            "fxpro_accounts": fxpro_accounts_count,
        },
        "chatterfy": {
            "tracker": "Chatterfy",
            "clicks": top_clicks,
            "attribution": top_attribution,
            "matched_clients": len(best_by_email),
        },
        "recent": recent,
    }

# Serve the built Telegram Mini App from the same HTTPS origin as the API.
# API routes are registered above, so this catch-all only handles frontend assets/pages.
if os.path.isdir("/app/web/dist"):
    app.mount("/", StaticFiles(directory="/app/web/dist", html=True), name="web")
