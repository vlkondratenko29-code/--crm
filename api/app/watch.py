"""Operator watch: reads Chatterfy chats and flags suspicious outgoing messages.

Pure helpers only (no database): text checks and a tiny Chatterfy API client.
main.py wires them to the database, the settings and Telegram alerts.
"""
import json
import re
import urllib.error
import urllib.request

CHATTERFY_API = "https://api.chatterfy.ai/api"

# Links and @usernames that belong to us and never raise an alert.
DEFAULT_ALLOWED = (
    "t.me/+8pRRehXlrqJkMTgy",        # free channel
    "t.me/+L18vLNfRqucxZmI6",        # premium channel
    "t.me/YusufFxGold",
    "redirect-fxpro.com/tr/partner/2YgRLAmfq",
    "@YusufFxGold",
)
OUR_PARTNER_CODE = "2YgRLAmfq"

# Code words from the free channel posts: a client who writes one came from that rubric.
DEFAULT_CODE_WORDS = ("VIP", "PAZARTESİ", "BAŞLA")

_B58 = "1-9A-HJ-NP-Za-km-z"
WALLET_PATTERNS = (
    ("TRC20-кошелёк", re.compile(r"(?<![A-Za-z0-9])T[%s]{33}(?![A-Za-z0-9])" % _B58)),
    ("ERC20/BEP20-кошелёк", re.compile(r"(?<![A-Za-z0-9])0x[a-fA-F0-9]{40}(?![A-Za-z0-9])")),
    ("BTC-кошелёк", re.compile(r"(?<![A-Za-z0-9])(?:bc1[a-z0-9]{25,62}|[13][%s]{25,34})(?![A-Za-z0-9])" % _B58)),
    ("TON-кошелёк", re.compile(r"(?<![A-Za-z0-9_-])(?:UQ|EQ)[A-Za-z0-9_-]{46}(?![A-Za-z0-9_-])")),
)
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+|(?<![\w./])(?:t\.me|telegram\.me|wa\.me)/[^\s<>\"']+", re.I)
MENTION_RE = re.compile(r"(?<![\w@./])@([A-Za-z][A-Za-z0-9_]{4,31})")
PHONE_RE = re.compile(r"(?<![\d+])(?:\+?90[\s-]?)?\(?0?5\d{2}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)|(?<![\d])\+\d{10,14}(?!\d)")
CONTACT_RE = re.compile(r"whats\s?app|watsap|vatsap|wp['’]?den|instagram|insta['’]?dan|signal\b|viber", re.I)


def _norm(text):
    return (text or "").lower().replace("https://", "").replace("http://", "").replace("www.", "")


def is_allowed(value, allowed):
    v = _norm(value).rstrip("/.,!?)")
    return any(_norm(a) and _norm(a) in v for a in allowed)


def check_text(text, allowed=DEFAULT_ALLOWED):
    """Return [(kind, match)] for everything in an outgoing message that should not be there."""
    if not text:
        return []
    found = []
    for kind, rx in WALLET_PATTERNS:
        for m in rx.finditer(text):
            found.append((kind, m.group(0)))
    urls = []
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".,!?)")
        urls.append(url)
        low = url.lower()
        if ("partner" in low or "ref=" in low or "affiliate" in low) and OUR_PARTNER_CODE.lower() not in low:
            found.append(("чужая партнёрская ссылка", url))
        elif "wa.me" in low:
            found.append(("WhatsApp", url))
        elif not is_allowed(url, allowed):
            found.append(("чужая ссылка", url))
    for m in MENTION_RE.finditer(text):
        if any(m.group(0).lower() in u.lower() for u in urls):
            continue
        if not is_allowed(m.group(0), allowed):
            found.append(("чужой @ник", m.group(0)))
    for m in PHONE_RE.finditer(text):
        found.append(("номер телефона", m.group(0).strip()))
    m = CONTACT_RE.search(text)
    if m:
        found.append(("увод в другой мессенджер", m.group(0)))
    seen, out = set(), []
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def code_word(text, words=DEFAULT_CODE_WORDS):
    """The channel code word a client's message contains, or None."""
    if not text:
        return None
    up = text.upper().replace("İ", "İ")
    for w in words:
        w_up = w.upper()
        variants = {w_up, w_up.replace("İ", "I").replace("Ş", "S")}
        for v in variants:
            if re.search(r"(?<![A-ZÇĞİÖŞÜ0-9])" + re.escape(v) + r"(?![A-ZÇĞİÖŞÜ0-9])", up):
                return w
    return None


class ChatterfyError(Exception):
    pass


class Chatterfy:
    def __init__(self, key, base=CHATTERFY_API, timeout=20):
        key = (key or "").strip()
        self.auth = key if key.lower().startswith("bearer ") else "Bearer " + key
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": self.auth, "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()[:200]
            except Exception:
                pass
            raise ChatterfyError(f"Chatterfy {method} {path}: HTTP {exc.code} {detail}".strip())
        except Exception as exc:
            raise ChatterfyError(f"Chatterfy {method} {path}: {type(exc).__name__}: {exc}")

    def chats(self, bot_id, limit=100, offset=0):
        r = self._req("POST", "/chats/v1/search", {"bot_id": bot_id, "limit": limit, "offset": offset, "full": True})
        return r.get("data") or []

    def messages(self, chat_id, limit=50):
        r = self._req("POST", "/messages/v1/search", {"chat_id": chat_id, "limit": limit})
        data = r.get("data") or {}
        return (data.get("items") if isinstance(data, dict) else data) or []

    def space_users(self, space_id):
        r = self._req("GET", f"/spaces/v1/{space_id}/users")
        return r.get("data") or []
