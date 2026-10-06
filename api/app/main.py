from datetime import datetime
import csv
import io

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr

from .config import is_admin

DB_PATH = os.getenv("DB_PATH", "broker_crm.db")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS broker_clients (email TEXT PRIMARY KEY, broker_id TEXT, status TEXT, country TEXT, click_id TEXT, registration_date TEXT, first_fund_date TEXT, first_fund_amount REAL, first_trade_date TEXT, last_trade_date TEXT, net_deposits REAL, deposits REAL, latest_balance REAL, trading_volume REAL)")
    conn.commit()
    return conn

app = FastAPI(title="Broker CRM API", version="0.4.0")


class LeadIn(BaseModel):
    email: EmailStr
    chat_id: str
    click_id: str | None = None


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


@app.post("/webhook/chatterfy")
def chatterfy_webhook(payload: LeadIn):
    return {
        "status": "received",
        "email": payload.email,
        "chat_id": payload.chat_id,
        "click_id": payload.click_id,
    }


@app.post("/api/v1/broker/fxpro/import")
async def import_fxpro_report(file: UploadFile = File(...)):
    raw = await file.read()
    clients = parse_fxpro_report(raw)
    conn = db()
    sql = """INSERT INTO broker_clients
    (email,broker_id,status,country,click_id,registration_date,first_fund_date,first_fund_amount,first_trade_date,last_trade_date,net_deposits,deposits,latest_balance,trading_volume)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(email) DO UPDATE SET broker_id=excluded.broker_id,status=excluded.status,country=excluded.country,click_id=excluded.click_id,registration_date=excluded.registration_date,first_fund_date=excluded.first_fund_date,first_fund_amount=excluded.first_fund_amount,first_trade_date=excluded.first_trade_date,last_trade_date=excluded.last_trade_date,net_deposits=excluded.net_deposits,deposits=excluded.deposits,latest_balance=excluded.latest_balance,trading_volume=excluded.trading_volume"""
    keys = ("email","broker_id","status","country","click_id","registration_date","first_fund_date","first_fund_amount","first_trade_date","last_trade_date","net_deposits","deposits","latest_balance","trading_volume")
    for client in clients:
        conn.execute(sql, tuple(client.get(k) for k in keys))
    conn.commit()
    conn.close()
    return {"status":"ok","broker":"FxPro","rows":len(clients)}

@app.get("/api/v1/clients/search")
def search_clients(q: str, x_telegram_username: str = Header(default="")):
    username = x_telegram_username.lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=401, detail="Telegram user is required")
    conn = db()
    term = "%" + q.strip().lower() + "%"
    rows = conn.execute("SELECT * FROM broker_clients WHERE lower(email) LIKE ? OR lower(coalesce(click_id,'')) LIKE ? LIMIT 20", (term, term)).fetchall()
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
    return {
        "leads": 184,
        "reg": 121,
        "ftd": 47,
        "ft": 31,
        "deposits": 12840,
        "viewer": {"username": username, "role": "admin" if is_admin(username) else "handler"},
    }
