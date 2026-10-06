from fastapi import FastAPI
from pydantic import BaseModel, EmailStr

app = FastAPI(title="Broker CRM API", version="0.1.0")

class LeadIn(BaseModel):
    email: EmailStr
    chat_id: str
    click_id: str | None = None

@app.get("/health")
def health():
    return {"status": "ok", "service": "broker-crm-api"}

@app.post("/webhook/chatterfy")
def chatterfy_webhook(payload: LeadIn):
    return {
        "status": "received",
        "email": payload.email,
        "chat_id": payload.chat_id,
        "click_id": payload.click_id,
    }

@app.get("/api/v1/dashboard")
def dashboard():
    return {
        "leads": 184,
        "reg": 121,
        "ftd": 47,
        "ft": 31,
        "deposits": 12840
    }
