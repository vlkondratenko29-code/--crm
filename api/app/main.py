from datetime import datetime
import csv
import io

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, EmailStr

from .config import is_admin

app = FastAPI(title="Broker CRM API", version="0.3.0")


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
    return {
        "status": "ok",
        "broker": "FxPro",
        "rows": len(clients),
        "clients": clients,
    }


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
