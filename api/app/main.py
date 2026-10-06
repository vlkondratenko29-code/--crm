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
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
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
            CREATE TABLE IF NOT EXISTS chatterfy_leads (
                chat_id TEXT PRIMARY KEY,
                email TEXT,
                click_id TEXT,
                last_synced_event TEXT
            )
        """)
        conn.commit()
        return conn

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
        CREATE TABLE IF NOT EXISTS chatterfy_leads (
            chat_id TEXT PRIMARY KEY,
            email TEXT,
            click_id TEXT,
            last_synced_event TEXT
        )
    """)
    conn.commit()
    return conn

app = FastAPI(title="Broker CRM API", version="0.5.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


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
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    clients = []
    for row in rows:
        email = (row.get("EmailAddress") or "").strip().lower()
        if not email:
            continue

        registration = parse_date(row.get("RegistrationDate"))
        first_fund = parse_date(row.get("First Fund Date"))
        first_trade = parse_date(row.get("First Trade Date"))

        events = []
        if registration:
            events.append({"type": "REG", "date": registration})
        if first_fund:
            events.append({
                "type": "FTD",
                "date": first_fund,
                "amount": to_float(row.get("First External Fund USD")),
            })
        if first_trade:
            events.append({"type": "FT", "date": first_trade})

        clients.append({
            "email": email,
            "broker_id": row.get("ProfileGUID"),
            "status": row.get("Status"),
            "country": row.get("Residential Country"),
            "click_id": row.get("ClickID") or None,
            "registration_date": registration,
            "first_fund_date": first_fund,
            "first_fund_amount": to_float(row.get("First External Fund USD")),
            "first_trade_date": first_trade,
            "last_trade_date": parse_date(row.get("Last Trade Date")),
            "net_deposits": to_float(row.get("NetDeposits USD (External)")),
            "deposits": to_float(row.get("Deposits USD (External)")),
            "latest_balance": to_float(row.get("Latest Balance USD")),
            "trading_volume": to_float(row.get("Trading Volume USD")),
            "events": events,
        })

    return clients

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


@app.get("/health")
def health():
    return {"status": "ok", "service": "broker-crm-api"}


@app.get("/api/v1/me", response_model=UserMe)
def me(x_telegram_username: str = Header(default="")):
    username = x_telegram_username.lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=401, detail="Telegram user is required")
    admin = is_admin(username)
    return {"username": username, "role": "admin" if admin else "handler", "is_admin": admin}


@app.api_route("/webhook/chatterfy", methods=["GET", "POST"])
async def chatterfy_webhook(request: Request, email: str | None = None, chat_id: str | None = None, click_id: str | None = None):
    if request.method == "POST":
        try:
            payload = await request.json()
            email = email or payload.get("email")
            chat_id = chat_id or payload.get("chat_id") or payload.get("chatId")
            click_id = click_id or payload.get("click_id") or payload.get("clickId")
        except Exception:
            pass
    if not email or not chat_id:
        raise HTTPException(status_code=400, detail="email and chat_id are required")
    conn = db()
    conn.execute("INSERT INTO chatterfy_leads(chat_id,email,click_id) VALUES(%s,%s,%s) ON CONFLICT(chat_id) DO UPDATE SET email=excluded.email, click_id=excluded.click_id", (chat_id, email.strip().lower(), click_id))
    conn.commit()
    conn.close()
    client = find_client(email, click_id)
    if not client:
        return {"status": "pending", "matched": False, "email": email, "chat_id": chat_id}
    sync = sync_client_to_chatterfy(chat_id, client)
    return {"status": "ok", "matched": True, "email": email, "chat_id": chat_id, "sync": sync}


@app.post("/api/v1/broker/fxpro/import")
async def import_fxpro_report(file: UploadFile = File(...), x_telegram_username: str = Header(default="")):
    username = x_telegram_username.lstrip("@").strip()
    if not username or not is_admin(username):
        raise HTTPException(status_code=403, detail="Admin access required")

    conn = None
    try:
        raw = await file.read()
        clients = parse_fxpro_report(raw)
        conn = db()
        sql = """INSERT INTO broker_clients
        (email,broker_id,status,country,click_id,registration_date,first_fund_date,first_fund_amount,first_trade_date,last_trade_date,net_deposits,deposits,latest_balance,trading_volume)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(email) DO UPDATE SET broker_id=excluded.broker_id,status=excluded.status,country=excluded.country,click_id=excluded.click_id,registration_date=excluded.registration_date,first_fund_date=excluded.first_fund_date,first_fund_amount=excluded.first_fund_amount,first_trade_date=excluded.first_trade_date,last_trade_date=excluded.last_trade_date,net_deposits=excluded.net_deposits,deposits=excluded.deposits,latest_balance=excluded.latest_balance,trading_volume=excluded.trading_volume"""
        keys = ("email","broker_id","status","country","click_id","registration_date","first_fund_date","first_fund_amount","first_trade_date","last_trade_date","net_deposits","deposits","latest_balance","trading_volume")
        for client in clients:
            conn.execute(sql, tuple(client.get(k) for k in keys))
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
        return {"status":"ok","broker":"FxPro","rows":len(clients),"chatterfy_synced":synced}
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
    if not username:
        raise HTTPException(status_code=401, detail="Telegram user is required")
    conn = db()
    term = "%" + q.strip().lower() + "%"
    rows = conn.execute("SELECT * FROM broker_clients WHERE lower(email) LIKE %s OR lower(coalesce(click_id,'')) LIKE %s LIMIT 20", (term, term)).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["events"] = []
        if item["registration_date"]: item["events"].append({"type":"REG","date":item["registration_date"]})
        if item["first_fund_date"]: item["events"].append({"type":"FTD","date":item["first_fund_date"],"amount":item["first_fund_amount"]})
        if item["first_trade_date"]: item["events"].append({"type":"FT","date":item["first_trade_date"]})
        result.append(item)
    return {"clients": result}


@app.get("/api/v1/dashboard")
def dashboard(x_telegram_username: str = Header(default="")):
    username = x_telegram_username.lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=401, detail="Telegram user is required")
    conn = db()
    row = conn.execute("""
        SELECT COUNT(*) AS leads,
               SUM(CASE WHEN registration_date IS NOT NULL THEN 1 ELSE 0 END) AS reg,
               SUM(CASE WHEN first_fund_date IS NOT NULL THEN 1 ELSE 0 END) AS ftd,
               SUM(CASE WHEN first_trade_date IS NOT NULL THEN 1 ELSE 0 END) AS ft,
               COALESCE(SUM(first_fund_amount), 0) AS deposits
        FROM broker_clients
    """).fetchone()
    conn.close()
    return {
        "leads": row["leads"] or 0,
        "reg": row["reg"] or 0,
        "ftd": row["ftd"] or 0,
        "ft": row["ft"] or 0,
        "deposits": row["deposits"] or 0,
        "viewer": {"username": username, "role": "admin" if is_admin(username) else "handler"},
    }


# Serve the built Telegram Mini App from the same HTTPS origin as the API.
# API routes are registered above, so this catch-all only handles frontend assets/pages.
if os.path.isdir("/app/web/dist"):
    app.mount("/", StaticFiles(directory="/app/web/dist", html=True), name="web")
