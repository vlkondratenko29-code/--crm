from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr
from .config import ADMINS, is_admin

app = FastAPI(title="Broker CRM API", version="0.2.0")

class LeadIn(BaseModel):
    email: EmailStr
    chat_id: str
    click_id: str | None = None

class UserMe(BaseModel):
    username: str
    role: str
    is_admin: bool

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
