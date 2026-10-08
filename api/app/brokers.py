"""Broker report parsing and lead ↔ broker account matching.

Pure functions only (no DB, no FastAPI) so they are easy to test.

Any broker's CSV export can be imported: columns are detected by name using
the synonym lists below (English and Russian). Matching a Chatterfy lead to a
broker account uses, in order of reliability:

1. label  — the broker report's Label / Sub ID / Click ID column equals the
            lead's Click ID (set when the affiliate link carries the Click ID)
2. email  — normalised email
3. phone  — last 10 digits of the phone number
"""
import csv
import io
import re
from datetime import datetime

# Canonical field -> accepted column names (compared case/space/punctuation-insensitively)
COLUMN_SYNONYMS = {
    "account_id": ["login", "логин", "account", "account id", "accountid", "account number", "номер счета",
                   "trader id", "traderid", "client id", "clientid", "user id", "userid", "customer id",
                   "profile guid", "profileguid", "id"],
    "email": ["email", "e-mail", "email address", "emailaddress", "почта", "электронная почта"],
    "phone": ["phone", "phone number", "mobile", "mobile phone", "телефон", "мобильный телефон", "tel"],
    "label": ["label", "лейбл", "метка", "sub id", "subid", "sub_id", "sub1", "aff_sub", "affsub",
              "click id", "clickid", "click_id", "tracking code", "tracking id", "tag", "campaign tag", "subaffiliate"],
    "name": ["name", "имя", "full name", "client name", "фио"],
    "country": ["country", "страна", "residential country"],
    "registration_date": ["registration date", "registrationdate", "дата регистрации", "datereg", "reg date",
                          "signup date", "created", "created at"],
    "ftd_date": ["first fund date", "firstfunddate", "ftd date", "first deposit date", "дата первого депозита"],
    "ftd_amount": ["first external fund usd", "first fund usd", "first fund amount", "ftd amount",
                   "first deposit amount", "first deposit", "сумма первого депозита"],
    "deposits": ["deposits", "депозиты", "deposits usd", "deposits usd (external)", "total deposits", "пополнения"],
    "withdrawals": ["withdrawals", "выводы", "withdrawals usd", "total withdrawals", "вывод средств"],
    "balance": ["balance", "баланс", "баланс в реальном времени", "real-time balance", "latest balance",
                "latest balance usd", "equity"],
    "last_trade_date": ["last trade", "last trade date", "lasttradedate", "последняя сделка"],
}

NUMERIC_FIELDS = {"ftd_amount", "deposits", "withdrawals", "balance"}
DATE_FIELDS = {"registration_date", "ftd_date", "last_trade_date"}


def _norm_header(text):
    return re.sub(r"[\s_\-\.\"'()]+", "", str(text or "").strip().lower().replace("﻿", ""))


_SYNONYM_INDEX = {}
for _field, _names in COLUMN_SYNONYMS.items():
    for _n in _names:
        _SYNONYM_INDEX.setdefault(_norm_header(_n), _field)


def normalize_email(value):
    value = str(value or "").strip().lower()
    return value if "@" in value and "." in value.split("@")[-1] else None


def normalize_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 7:
        return None
    return digits[-10:]


def normalize_account_id(value):
    """Broker account number / UID as typed by an operator: digits only, 4+ long."""
    text = str(value or "").strip()
    if not re.fullmatch(r"#?\d[\d\s-]{3,}", text):
        return None  # GUIDs, emails, names are not account numbers
    return re.sub(r"\D", "", text)


def normalize_label(value):
    value = str(value or "").strip().lower()
    if not value or value in {"-", "—", "none", "null", "n/a"} or "{{" in value:
        return None
    return value


def parse_any_date(value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text or text in {"-", "—"}:
        return None
    text = re.sub(r"(\.\d+)0$", r"\1", text)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:26], fmt).date().isoformat()
        except ValueError:
            pass
    return text[:10]


def parse_amount(value):
    if value in (None, ""):
        return None
    text = str(value).strip().replace(" ", "").replace(" ", "").replace("$", "")
    if not text or text in {"-", "—"}:
        return None
    # "1.234,56" -> 1234.56 ; "1,234.56" -> 1234.56 ; "1234,56" -> 1234.56
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".") if len(text.split(",")[-1]) <= 2 else text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def detect_columns(headers):
    """Map canonical field -> original header. First matching header wins."""
    mapping = {}
    for header in headers:
        field = _SYNONYM_INDEX.get(_norm_header(header))
        if field and field not in mapping:
            mapping[field] = header
    return mapping


def read_csv(raw: bytes):
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:10000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return reader.fieldnames or [], list(reader)


def parse_broker_report(raw: bytes):
    """Parse any broker CSV into normalised account dicts.

    Returns (accounts, mapping). Rows without an account id fall back to the
    email (or phone) as the id so they can still be matched.
    """
    headers, rows = read_csv(raw)
    mapping = detect_columns(headers)
    accounts = []
    for row in rows:
        def get(field):
            header = mapping.get(field)
            return row.get(header) if header else None

        email = normalize_email(get("email"))
        phone = normalize_phone(get("phone"))
        account_id = str(get("account_id") or "").strip()
        if not account_id or "@" in account_id:
            account_id = email or (f"tel:{phone}" if phone else "")
        if not account_id:
            continue
        withdrawals = parse_amount(get("withdrawals"))
        if withdrawals is not None:
            withdrawals = abs(withdrawals)
        accounts.append({
            "account_id": account_id,
            "email": email,
            "phone": phone,
            "label": normalize_label(get("label")),
            "name": (get("name") or "").strip() or None,
            "country": (get("country") or "").strip() or None,
            "registration_date": parse_any_date(get("registration_date")),
            "ftd_date": parse_any_date(get("ftd_date")),
            "ftd_amount": parse_amount(get("ftd_amount")),
            "deposits": parse_amount(get("deposits")),
            "withdrawals": withdrawals,
            "balance": parse_amount(get("balance")),
            "last_trade_date": parse_any_date(get("last_trade_date")),
        })
    return accounts, mapping


def build_account_index(accounts):
    """Index broker accounts by label, email and phone for O(1) matching."""
    index = {"label": {}, "email": {}, "phone": {}, "account_id": {}}
    for acc in accounts:
        acc_id = normalize_account_id(acc.get("account_id"))
        if acc_id:
            index["account_id"].setdefault(acc_id, []).append(acc)
        for key in ("label", "email", "phone"):
            value = acc.get(key)
            if key == "label":
                value = normalize_label(value)
            elif key == "email":
                value = normalize_email(value)
            else:
                value = normalize_phone(value)
            if value:
                index[key].setdefault(value, []).append(acc)
    return index


def match_lead(lead, index):
    """Return (method, accounts) for the most reliable match, or (None, [])."""
    for uid in lead.get("account_ids") or []:
        hit = index.get("account_id", {}).get(normalize_account_id(uid) or "")
        if hit:
            return "uid", hit
    for label in lead.get("labels") or []:
        hit = index["label"].get(normalize_label(label) or "")
        if hit:
            return "label", hit
    email = normalize_email(lead.get("email"))
    if email and email in index["email"]:
        return "email", index["email"][email]
    phone = normalize_phone(lead.get("phone"))
    if phone and phone in index["phone"]:
        return "phone", index["phone"][phone]
    return None, []


def account_has_deposit(acc):
    return bool(acc.get("ftd_date")) or float(acc.get("deposits") or 0) > 0 or float(acc.get("ftd_amount") or 0) > 0
