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


@app.api_route("/webhook/chatterfy", methods=["GET", "POST"])
async def chatterfy_webhook(
    request: Request,
    email: str | None = None, chat_id: str | None = None, click_id: str | None = None,
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
            for key in attribution:
                attribution[key] = attribution[key] or payload.get(key)
        except Exception:
            pass
    if not email or not chat_id:
        raise HTTPException(status_code=400, detail="email and chat_id are required")
    import json
    attribution_json = json.dumps({k:v for k,v in attribution.items() if v not in (None, "")}, ensure_ascii=False)
    conn = db()
    conn.execute("INSERT INTO chatterfy_leads(chat_id,email,click_id,attribution_json) VALUES(%s,%s,%s,%s) ON CONFLICT(chat_id) DO UPDATE SET email=excluded.email, click_id=excluded.click_id, attribution_json=excluded.attribution_json", (chat_id, email.strip().lower(), click_id, attribution_json))
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
    user = require_access(username)
    if user["role"] != "admin":
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
    conn.close()
    import json
    result = []
    for row in rows:
        item = dict(row)
        raw_attr = item.pop("chatterfy_attribution_json", None)
        chatterfy_click_id = item.pop("chatterfy_click_id", None)
        try:
            item["attribution"] = json.loads(raw_attr) if raw_attr else {}
        except Exception:
            item["attribution"] = {}
        if chatterfy_click_id and not item.get("click_id"):
            item["click_id"] = chatterfy_click_id
        if user["role"] == "seo":
            for key in ("first_fund_amount", "net_deposits", "deposits", "latest_balance", "trading_volume"):
                item[key] = None
        item["events"] = []
        if item["registration_date"]: item["events"].append({"type":"REG","date":item["registration_date"]})
        if item["first_fund_date"]: item["events"].append({"type":"FTD","date":item["first_fund_date"],"amount":item["first_fund_amount"]})
        if item["first_trade_date"]: item["events"].append({"type":"FT","date":item["first_trade_date"]})
        result.append(item)
    return {"clients": result}


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
    for r in lead_rows:
        try:
            attr = json.loads(r["attribution_json"]) if r["attribution_json"] else {}
        except Exception:
            attr = {}
        campaign = attr.get("tracker_campaign") or attr.get("tracker_campaign_name") or "Unknown"
        source = attr.get("tracker_source") or attr.get("tracker_source_name") or "Unknown"
        adset = attr.get("adset_name") or attr.get("adset_id") or "Unknown"
        ad = attr.get("ad_id") or "Unknown"
        placement = attr.get("placement") or "Unknown"
        click = (r["chatterfy_click_id"] or r["click_id"] or "").strip() or "No Click ID"
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

    top_countries = sorted(countries.items(), key=lambda x: x[1], reverse=True)[:5]
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
        "chatterfy": {"tracker": "Chatterfy", "clicks": top_clicks, "attribution": top_attribution},
        "recent": recent,
    }

# Serve the built Telegram Mini App from the same HTTPS origin as the API.
# API routes are registered above, so this catch-all only handles frontend assets/pages.
if os.path.isdir("/app/web/dist"):
    app.mount("/", StaticFiles(directory="/app/web/dist", html=True), name="web")
