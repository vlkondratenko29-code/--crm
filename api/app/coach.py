"""Dialog review for handlers: finds likely mistakes in an operator's Chatterfy chats so they can learn.

Two layers, both pure (no database):
- rule checks: slow replies, a client left waiting, promises of profit, foreign contacts, walls of text,
  no registration link — free and instant;
- an AI review (Claude) when ANTHROPIC_API_KEY is set: reads the whole chat like a team lead would and
  says what went wrong, quoting the message and suggesting a better reply.

A chat is {"id", "name", "link", "started", "messages": [{"at", "role", "who", "text"}]} where role is
client / bot / operator (the reviewed one) / other (another operator).
"""
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import watch as watch_lib

SLOW_REPLY_MIN = 10          # client waited longer than this for the operator
SHIFT_GAP_HOURS = 12         # longer gaps are off-shift, not a slow reply
WAITING_HOURS = 1            # last word is the client's and nobody answered for this long
LONG_MESSAGE = 700           # characters: a wall of text nobody reads in a messenger
MIN_OPERATOR_MESSAGES = 5    # below this a chat is too short to expect a registration link
REG_MARKERS = ("redirect-fxpro", watch_lib.OUR_PARTNER_CODE.lower(), "fxpro.com")

PROMISE_RE = re.compile(
    r"garanti|kesin\s+kazan|%\s?100\s+kazan|100\s?%\s+kazan|risksiz|risk\s+yok|zarar\s+(?:etmez|yok)|kaybetmez"
    r"|гарант|без\s+риска|точно\s+заработа|guarantee|no\s+risk|risk[-\s]free",
    re.I,
)

KIND_INFO = {
    "slow": ("Долгий ответ", "medium"),
    "waiting": ("Клиент остался без ответа", "high"),
    "promise": ("Обещание дохода / «без риска»", "high"),
    "contact": ("Чужой контакт или ссылка", "high"),
    "long": ("Слишком длинное сообщение", "low"),
    "no_link": ("Не дал ссылку на регистрацию", "medium"),
}


def _t(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _finding(kind, at=None, quote="", problem="", better=""):
    title, severity = KIND_INFO[kind]
    return {"source": "rule", "kind": kind, "title": title, "severity": severity, "at": at,
            "quote": (quote or "")[:300], "problem": problem, "better": better}


def rule_findings(chat, allowed=watch_lib.DEFAULT_ALLOWED, now=None):
    now = now or datetime.utcnow()
    msgs = chat["messages"]
    own = [m for m in msgs if m["role"] == "operator"]
    if not own:
        return []
    out = []
    joined = _t(own[0]["at"])

    # Slow replies after the operator took the chat: first unanswered client message -> next live answer.
    waiting_since, waiting_msg = None, None
    for m in msgs:
        at = _t(m["at"])
        if m["role"] == "client":
            if waiting_since is None and at and joined and at >= joined:
                waiting_since, waiting_msg = at, m
        elif m["role"] in ("operator", "other"):
            if waiting_since and at:
                gap = (at - waiting_since).total_seconds() / 60
                if m["role"] == "operator" and SLOW_REPLY_MIN < gap < SHIFT_GAP_HOURS * 60:
                    out.append(_finding("slow", waiting_msg["at"], waiting_msg["text"],
                                        f"Клиент ждал ответа {int(gap)} мин.",
                                        f"Отвечай в течение {SLOW_REPLY_MIN} минут — пока клиент тёплый. "
                                        "Если нужно время, напиши коротко «сейчас проверю и отвечу»."))
            waiting_since, waiting_msg = None, None
        elif m["role"] == "bot":
            waiting_since, waiting_msg = None, None

    last = msgs[-1]
    last_at = _t(last["at"])
    if last["role"] == "client" and last_at and (now - last_at).total_seconds() > WAITING_HOURS * 3600:
        hours = int((now - last_at).total_seconds() // 3600)
        out.append(_finding("waiting", last["at"], last["text"],
                            f"Последнее сообщение от клиента, ответа нет уже {hours} ч.",
                            "Ответь клиенту или передай чат тому, кто на смене. Молчание — самый частый слив лида."))

    for m in own:
        text = m["text"] or ""
        hit = PROMISE_RE.search(text)
        if hit:
            out.append(_finding("promise", m["at"], text,
                                f"Обещание результата («{hit.group(0)}»). Это вводит клиента в заблуждение и даёт повод для жалоб.",
                                "Говори про возможности и про риск честно: «трейдинг связан с риском, начнём с небольшой суммы»."))
        for kind, match in watch_lib.check_text(text, allowed):
            out.append(_finding("contact", m["at"], text, f"{kind}: {match}",
                                "В чате только наши ссылки и наш канал. Чужие контакты, кошельки и номера запрещены."))
        if len(text) > LONG_MESSAGE:
            out.append(_finding("long", m["at"], text, f"Сообщение на {len(text)} символов — в мессенджере такое не дочитывают.",
                                "Разбей на 2–3 коротких сообщения и закончи вопросом, чтобы клиент ответил."))

    outgoing = " ".join((m["text"] or "").lower() for m in msgs if m["role"] != "client")
    if len(own) >= MIN_OPERATOR_MESSAGES and not any(x in outgoing for x in REG_MARKERS):
        out.append(_finding("no_link", own[-1]["at"], "",
                            f"Оператор написал {len(own)} сообщений, но ссылки на регистрацию в чате нет.",
                            "Веди разговор к следующему шагу: выясни интерес → дай ссылку на регистрацию → помоги пройти её."))
    return out


# --- AI review -------------------------------------------------------------------------------------

AI_MODEL = os.getenv("COACH_MODEL", "claude-opus-5-5")
AI_WORKERS = 5

AI_CATEGORIES = [
    "Не ответил на вопрос", "Упустил готового клиента", "Нет следующего шага", "Давление / грубость",
    "Обещание дохода", "Неверная информация", "Шаблонно / не по ситуации", "Язык и грамотность", "Другое",
]

AI_SYSTEM = """Ты — опытный тимлид отдела обработки лидов. Ты разбираешь переписку обработчика с клиентом, чтобы обработчик учился на своих ошибках.

Контекст работы: клиенты (в основном из Турции, пишут по-турецки) приходят с рекламы в Telegram-бота. Сначала отвечает бот/AI, потом подключается живой обработчик. Его задача — честно и по-человечески довести клиента до регистрации у брокера FxPro по нашей ссылке, помочь пройти верификацию и сделать первый депозит, а дальше поддерживать.

Что считается ошибкой обработчика:
- не ответил на прямой вопрос клиента или ответил не по сути;
- клиент был готов (спрашивал, как начать, куда платить, как зарегистрироваться), а обработчик не довёл до действия;
- разговор закончился без следующего шага (нет вопроса, нет ссылки, нет договорённости);
- давление, грубость, спор с клиентом, игнорирование возражения;
- обещание дохода, «без риска», гарантии;
- неверная или путаная информация про регистрацию, депозит, вывод;
- шаблонные ответы, которые не учитывают то, что написал клиент;
- ошибки в турецком, из-за которых непонятно или выглядит несерьёзно.

Правила:
- Оценивай ТОЛЬКО сообщения с пометкой «ОБРАБОТЧИК». Бот и другие операторы — контекст, их ошибки не разбирай.
- Указывай только настоящие ошибки, которые стоит разобрать. Не придирайся. Если всё хорошо — список ошибок пустой.
- quote — дословная цитата сообщения обработчика (или клиента, если ошибка в том, что на него не ответили), до 200 символов.
- problem — коротко по-русски, что не так и чем это плохо.
- better — как надо было: по-русски объясни и дай пример готовой фразы на языке клиента.
- good — 1–3 вещи, которые обработчик сделал хорошо (по-русски), если есть.
- score — оценка работы обработчика в этом чате от 1 до 10.
- summary — один-два предложения итога по-русски."""

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "summary": {"type": "string"},
        "good": {"type": "array", "items": {"type": "string"}},
        "mistakes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": AI_CATEGORIES},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "quote": {"type": "string"},
                    "problem": {"type": "string"},
                    "better": {"type": "string"},
                },
                "required": ["category", "severity", "quote", "problem", "better"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["score", "summary", "good", "mistakes"],
    "additionalProperties": False,
}

_ROLE_LABEL = {"client": "КЛИЕНТ", "bot": "БОТ/AI", "operator": "ОБРАБОТЧИК", "other": "ДРУГОЙ ОПЕРАТОР"}


def ai_enabled():
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def transcript(chat, fmt_ts=lambda v: v or "??"):
    lines = [f"[{fmt_ts(m['at'])}] {_ROLE_LABEL[m['role']]}: {m['text'] or '[вложение/медиа]'}" for m in chat["messages"]]
    return "\n".join(lines)


def _ai_review_chat(client, chat, fmt_ts):
    import anthropic
    try:
        response = client.beta.messages.create(
            model=AI_MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=AI_SYSTEM,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
            messages=[{"role": "user", "content": "Переписка:\n\n" + transcript(chat, fmt_ts)}],
        )
    except anthropic.AuthenticationError:
        return {"error": "Неверный ANTHROPIC_API_KEY"}
    except anthropic.RateLimitError:
        return {"error": "Лимит запросов Claude, попробуй позже"}
    except anthropic.APIStatusError as exc:
        return {"error": f"Claude: HTTP {exc.status_code}"}
    except anthropic.APIConnectionError:
        return {"error": "Claude недоступен (сеть)"}
    if response.stop_reason == "refusal":
        return {"error": "Claude отказался разбирать этот чат"}
    if response.stop_reason == "max_tokens":
        return {"error": "Ответ Claude обрезан"}
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError:
        return {"error": "Claude вернул неразборчивый ответ"}
    data["score"] = max(1, min(10, int(data.get("score") or 0) or 1))
    return data


def ai_review(chats, fmt_ts):
    """chat id -> {score, summary, good, mistakes} or {error}. Chats run in parallel."""
    if not chats or not ai_enabled():
        return {}
    import anthropic
    client = anthropic.Anthropic(timeout=180, max_retries=3)
    with ThreadPoolExecutor(max_workers=AI_WORKERS) as pool:
        results = pool.map(lambda c: _ai_review_chat(client, c, fmt_ts), chats)
        return {c["id"]: r for c, r in zip(chats, results)}


def review(chats, allowed=watch_lib.DEFAULT_ALLOWED, fmt_ts=lambda v: v or "??", now=None):
    """Full report for one operator: per chat findings plus a summary of what repeats."""
    ai = ai_review([c for c in chats if any(m["role"] == "operator" for m in c["messages"])], fmt_ts)
    out_chats, counts, scores = [], {}, []
    for c in chats:
        findings = rule_findings(c, allowed, now)
        a = ai.get(c["id"]) or {}
        for m in a.get("mistakes") or []:
            findings.append({"source": "ai", "kind": "ai", "title": m["category"], "severity": m["severity"], "at": None,
                             "quote": m["quote"], "problem": m["problem"], "better": m["better"]})
        for f in findings:
            counts[f["title"]] = counts.get(f["title"], 0) + 1
        if a.get("score"):
            scores.append(a["score"])
        rank = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: rank.get(f["severity"], 3))
        out_chats.append({
            "id": c["id"], "name": c["name"], "link": c["link"], "started": c.get("started"),
            "operator_messages": sum(1 for m in c["messages"] if m["role"] == "operator"),
            "score": a.get("score"), "summary": a.get("summary"), "good": a.get("good") or [],
            "ai_error": a.get("error"), "findings": findings,
        })
    out_chats.sort(key=lambda c: (-sum(1 for f in c["findings"] if f["severity"] == "high"), -len(c["findings"])))
    top = sorted(counts.items(), key=lambda kv: -kv[1])
    return {
        "chats": out_chats,
        "top": [{"title": k, "count": v} for k, v in top],
        "findings": sum(counts.values()),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "ai": ai_enabled(),
    }
