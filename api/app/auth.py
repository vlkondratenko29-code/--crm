"""Telegram Mini App authentication.

The Mini App sends `Telegram.WebApp.initData` in the `X-Telegram-Init-Data`
header. Telegram signs that string with the bot token, so the backend can
trust the user inside it only after the HMAC check below passes.
Docs: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import os
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
    secret_key = hmac.new(b"WebAppData", bot_token.strip().encode(), hashlib.sha256).digest()

    def digest(fields):
        data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
        return hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    # The HMAC covers every field except `hash` (including the newer
    # `signature` field). Clients that predate `signature` simply omit it.
    # Also accept the variant without `signature` for older client builds.
    without_signature = {k: v for k, v in pairs.items() if k != "signature"}
    if not (hmac.compare_digest(digest(pairs), received_hash)
            or hmac.compare_digest(digest(without_signature), received_hash)):
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



# ---------------------------------------------------------------------------
# CRM website: login + password, then a signed session (nothing stored on the server)
# ---------------------------------------------------------------------------

PASSWORD_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = (stored or "").split("$")
        if scheme != "pbkdf2":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual.hex(), digest)


def password_version(stored: str) -> str:
    """Short fingerprint of the password hash: a new password signs every old session out."""
    return hashlib.sha256((stored or "").encode()).hexdigest()[:12]


def _session_key(secret: str) -> bytes:
    return hashlib.sha256(b"crm-session:" + secret.strip().encode()).digest()


def make_session(username: str, version: str, secret: str, days: int = 30):
    """A signed website session: "username.expires.version.signature"."""
    expires = int(time.time()) + days * 24 * 3600
    body = f"{username}.{expires}.{version}"
    sig = hmac.new(_session_key(secret), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}", expires


def read_session(token: str, secret: str):
    """(username, password version) from a valid, unexpired session token, else None."""
    try:
        username, expires, version, sig = (token or "").strip().split(".")
        expires_at = int(expires)
    except ValueError:
        return None
    body = f"{username}.{expires}.{version}"
    expected = hmac.new(_session_key(secret), body.encode(), hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(expected, sig) or expires_at < time.time():
        return None
    return username, version
