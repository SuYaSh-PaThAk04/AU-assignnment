"""Header, phone, date, money, enum, and timestamp normalization."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

IST = timezone(timedelta(hours=5, minutes=30))

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_MONEY_PREFIX = re.compile(r"^(?:₹|rs\.|rs|inr)\s*", re.IGNORECASE)


def norm_header(value: str) -> str:
    """Lower-case, trim, and collapse '_' or whitespace into a single space."""
    text = value.strip().lower()
    text = re.sub(r"[_\s]+", " ", text)
    return text.strip()


def normalize_phone(value: str) -> str | None:
    """Return +91XXXXXXXXXX, or None when the number is not a valid Indian mobile."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


def _valid_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(value: str) -> date | None:
    """Parse a day-first date. Impossible dates such as 31/02 return None."""
    text = value.strip()
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
        return _valid_date(year, month, day)
    slash = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", text)
    if slash:
        day, month, year = (int(part) for part in slash.groups())
        return _valid_date(year, month, day)
    named = re.fullmatch(r"(\d{2})-([A-Za-z]{3})-(\d{4})", text)
    if named:
        day = int(named.group(1))
        month = _MONTHS.get(named.group(2).lower())
        year = int(named.group(3))
        if month is None:
            return None
        return _valid_date(year, month, day)
    dashed = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", text)
    if dashed:
        day, month, year = (int(part) for part in dashed.groups())
        return _valid_date(year, month, day)
    return None


def parse_decimal(value: str) -> Decimal | None:
    """Parse a non-negative money amount and round half-up to 2 places."""
    text = value.strip()
    text = _MONEY_PREFIX.sub("", text)
    text = text.replace(",", "").strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def parse_enum(value: str, allowed: list[str]) -> str | None:
    """Match an enum case-insensitively and return the lower-case code."""
    text = value.strip().lower()
    allowed_lower = {item.lower() for item in allowed}
    if text in allowed_lower:
        return text
    return None


def parse_timestamp(value: str) -> datetime | None:
    """Parse UTC, offset, or naive timestamps. Naive values are IST."""
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        base = text[:-1]
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(base, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None
    if re.search(r"[+-]\d{2}:\d{2}$", text):
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def to_ist(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=IST)
    return moment.astimezone(IST)


def format_ist(moment: datetime) -> str:
    return to_ist(moment).isoformat(timespec="seconds")
