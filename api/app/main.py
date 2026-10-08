    return None


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
    import json
    attribution = {k: clean_attribution_value(v) for k, v in attribution.items()}
    attribution_json = json.dumps({k:v for k,v in attribution.items() if v is not None}, ensure_ascii=False)
    conn = db()
    normalized_email = email.strip().lower() if email else None
    conn.execute("INSERT INTO chatterfy_leads(chat_id,email,click_id,attribution_json) VALUES(%s,%s,%s,%s) ON CONFLICT(chat_id) DO UPDATE SET email=COALESCE(excluded.email,chatterfy_leads.email), click_id=COALESCE(excluded.click_id,chatterfy_leads.click_id), attribution_json=excluded.attribution_json", (chat_id, normalized_email, click_id, attribution_json))
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