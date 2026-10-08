"""Telegram Mini App authentication.

The Mini App sends `Telegram.WebApp.initData` in the `X-Telegram-Init-Data`
header. Telegram signs that string with the bot token, so the backend can
trust the user inside it only after the HMAC check below passes.
Docs: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 7 * 24 * 3600):
    """Return the Telegram user dict if `init_data` is authentic, else None."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    # `signature` is the newer Ed25519 field; it is not part of the HMAC check.
    pairs.pop("signature", None)
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age_seconds and time.time() - auth_date > max_age_seconds:
        return None
    try:
        user = json.loads(pairs.get("user") or "{}")
    except ValueError:
        return None
    return user if isinstance(user, dict) and user.get("id") else None
