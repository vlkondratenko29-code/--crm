"""Facebook Marketing API: daily spend per campaign / ad set / ad, the same rows as an Ads Manager CSV.

Needs a long-lived token with `ads_read` (Business Manager → System users → Generate token) and the
ad account ids (`act_123...` or just the digits). Docs:
https://developers.facebook.com/docs/marketing-api/insights
"""
import json
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v21.0"
FIELDS = "campaign_name,adset_name,ad_name,spend,impressions,inline_link_clicks,account_currency,date_start"


def account_id(raw):
    raw = str(raw or "").strip()
    return raw if raw.startswith("act_") else f"act_{raw}"


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode()).get("error", {}).get("message")
        except Exception:
            msg = None
        raise RuntimeError(msg or f"HTTP {exc.code}") from None


def fetch_spend(token, account, since, until):
    """Rows {day, campaign, adset, ad, spend, impressions, clicks, currency} for since..until (YYYY-MM-DD)."""
    params = urllib.parse.urlencode({
        "level": "ad", "time_increment": 1, "fields": FIELDS, "limit": 500,
        "time_range": json.dumps({"since": since, "until": until}), "access_token": token,
    })
    url = f"{GRAPH}/{account_id(account)}/insights?{params}"
    rows = []
    while url:
        page = _get(url)
        for r in page.get("data", []):
            rows.append({
                "day": r.get("date_start"), "campaign": (r.get("campaign_name") or "").strip(),
                "adset": (r.get("adset_name") or "").strip(), "ad": (r.get("ad_name") or "").strip(),
                "spend": float(r.get("spend") or 0), "impressions": float(r.get("impressions") or 0),
                "clicks": float(r.get("inline_link_clicks") or 0), "currency": r.get("account_currency") or "USD",
            })
        url = page.get("paging", {}).get("next")
    return [r for r in rows if r["day"] and r["campaign"]]
