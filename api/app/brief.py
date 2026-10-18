"""Short lead description from the Chatterfy dialog, without AI: who talked, what the client asked,
what they said about money / trust / timing, how far the funnel went and where the dialog stands.

Pure helpers (no database). A message is {"at", "role", "who", "text"} with role client / bot / staff.
"""
import re
from datetime import datetime

from . import watch as watch_lib

# Topics in the client's messages (Turkish first, then Russian / English). Order = display order.
TOPICS = (
    ("start", "Хочет начать", r"nasıl\s+başla|başlamak\s+istiyorum|katılmak\s+istiyorum|başlayalım|ne\s+yapmam\s+gerek|как\s+начать|хочу\s+начать|how\s+to\s+start"),
    ("deposit", "Говорит про депозит", r"yatırdım|yatırırım|yatırabilirim|yatıracağım|para\s+yatır|yatırım\s+yap|deposit|депозит|пополн"),
    ("register", "Регистрация / верификация", r"kayıt|kaydol|üye\s+ol|doğrulama|kimlik|belge|onay\s+bekl|verif|регистрац|верификац"),
    ("withdraw", "Спрашивает про вывод", r"para\s+çek|çekim|çekil|çekme|çekebilir|withdraw|вывод|вывести"),
    ("no_money", "Нет денег / мало бюджета", r"param\s+yok|para\s+yok|bütçe|maaş|imkanım\s+yok|нет\s+денег|no\s+money"),
    ("distrust", "Не доверяет", r"dolandırıc|güvenmiyorum|güvenebilir|sahte|kandır|scam|мошен|развод|обман"),
    ("risk", "Боится потерять деньги", r"korku|korkuyorum|kaybed|kayıp|riskli|боюсь|потеря"),
    ("later", "Откладывает", r"daha\s+sonra|sonra\s+yaz|yarın|müsait\s+değilim|şimdi\s+değil|düşüneceğim|потом|позже|завтра|later"),
    ("refuse", "Отказывается", r"istemiyorum|ilgilenmiyorum|rahatsız\s+etme|yazma\s+bana|engelle|не\s+интересно|не\s+пишите|not\s+interested"),
)
_TOPIC_RE = [(k, t, re.compile(rx, re.I)) for k, t, rx in TOPICS]

AMOUNT_RE = re.compile(r"(?<![\w.])(\d{2,6}(?:[.,]\d{1,2})?)\s?(\$|usd|dolar|dollar|tl|₺|lira|€|euro|eur|руб)(?!\w)"
                       r"|(\$|€|₺)\s?(\d{2,6}(?:[.,]\d{1,2})?)(?![\d])", re.I)
QUESTION_RE = re.compile(r"\?|\b(?:nasıl|neden|nerede|ne\s+zaman|ne\s+kadar|kaç|hangi|kim)\b"
                         r"|\b\w+\s+(?:mı|mi|mu|mü|mısın|misin|musun|müsün)\b", re.I)
UID_RE = re.compile(r"(?<!\d)\d{7,10}(?!\d)")
REG_MARKERS = ("redirect-fxpro", watch_lib.OUR_PARTNER_CODE.lower())


def _t(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _short(text, n=160):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _ago(at, now):
    t = _t(at)
    if not t:
        return ""
    mins = int((now - t).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)} мин"
    if mins < 48 * 60:
        return f"{mins // 60} ч"
    return f"{mins // 1440} дн"


def lead_brief(messages, fmt_ts=lambda v: v or "—", now=None):
    now = now or datetime.utcnow()
    msgs = sorted([m for m in messages if m.get("at")], key=lambda m: m["at"])
    if not msgs:
        return {"empty": True, "summary": "В чате пока нет сообщений.", "facts": [], "topics": [], "questions": []}
    client = [m for m in msgs if m["role"] == "client"]
    staff = [m for m in msgs if m["role"] == "staff"]
    bot = [m for m in msgs if m["role"] == "bot"]
    handlers = []
    for m in staff:
        if m["who"] not in handlers:
            handlers.append(m["who"])

    facts = [
        {"label": "Диалог", "value": f"с {fmt_ts(msgs[0]['at'])} · {len(client)} от клиента, {len(staff)} от обработчиков, {len(bot)} от бота"},
        {"label": "Обработчик", "value": ", ".join(handlers) if handlers else "живой обработчик ещё не писал"},
    ]
    if staff:
        facts.append({"label": "Первый живой ответ", "value": fmt_ts(staff[0]["at"])})

    # Funnel steps visible in the chat.
    link = next((m for m in msgs if m["role"] != "client" and any(x in (m["text"] or "").lower() for x in REG_MARKERS)), None)
    facts.append({"label": "Ссылка на регистрацию", "value": f"отправлена {fmt_ts(link['at'])}" if link else "не отправлялась"})
    emails = [e for m in client for e in watch_lib.emails_in(m["text"])]
    uids = [u for m in client for u in UID_RE.findall(m["text"] or "")]
    if emails or uids:
        facts.append({"label": "Клиент прислал", "value": ", ".join(([f"почту {emails[-1]}"] if emails else []) + ([f"UID {uids[-1]}"] if uids else []))})
    amounts = []
    for m in client:
        for a in AMOUNT_RE.finditer(m["text"] or ""):
            value = (a.group(1) or a.group(4)) + " " + (a.group(2) or a.group(3))
            if value not in amounts:
                amounts.append(value)
    if amounts:
        facts.append({"label": "Называл суммы", "value": ", ".join(amounts[-3:])})

    topics, seen = [], set()
    for k, title, rx in _TOPIC_RE:
        hit = next((m for m in reversed(client) if rx.search(m["text"] or "")), None)
        if hit and k not in seen:
            seen.add(k)
            topics.append({"key": k, "title": title, "quote": _short(hit["text"]), "at": hit["at"]})

    questions = [{"quote": _short(m["text"]), "at": m["at"]} for m in client if QUESTION_RE.search(m["text"] or "")][-3:]

    last = msgs[-1]
    if last["role"] == "client":
        state = f"Ждёт ответа {_ago(last['at'], now)}"
        state_kind = "waiting"
    elif client and _t(client[-1]["at"]) and (now - _t(client[-1]["at"])).total_seconds() > 24 * 3600:
        state = f"Клиент молчит {_ago(client[-1]['at'], now)} после нашего сообщения"
        state_kind = "silent"
    elif not client:
        state = "Клиент ещё ничего не писал"
        state_kind = "silent"
    else:
        state = f"Последним ответили мы, {_ago(last['at'], now)} назад"
        state_kind = "ok"

    # One-paragraph summary assembled from the facts above.
    parts = []
    parts.append(f"Ведёт {', '.join(handlers)}." if handlers else "Пока общался только бот.")
    if topics:
        parts.append("Клиент: " + ", ".join(t["title"].lower() for t in topics) + ".")
    if amounts:
        parts.append(f"Называл сумму {amounts[-1]}.")
    parts.append("Ссылку на регистрацию дали." if link else "Ссылку на регистрацию ещё не дали.")
    if emails or uids:
        parts.append("Данные для сверки прислал.")
    parts.append(state + ".")

    return {
        "empty": False,
        "summary": " ".join(parts),
        "state": state, "state_kind": state_kind,
        "facts": facts,
        "topics": topics,
        "questions": questions,
        "last_client": {"quote": _short(client[-1]["text"], 240), "at": client[-1]["at"]} if client else None,
    }
