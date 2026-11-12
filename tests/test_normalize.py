from datetime import date, datetime, timezone
from decimal import Decimal

from solution.normalize import (
    IST,
    format_ist,
    norm_header,
    normalize_phone,
    parse_date,
    parse_decimal,
    parse_enum,
    parse_timestamp,
)


def test_header_collapses_case_underscores_and_whitespace():
    assert norm_header("  Policy_No ") == "policy no"
    assert norm_header("Mobile   No") == "mobile no"
    assert norm_header("preferred_language") == "preferred language"


def test_phone_accepts_the_six_generator_shapes():
    canonical = "+919876543210"
    assert normalize_phone("9876543210") == canonical
    assert normalize_phone("+91 98765 43210") == canonical
    assert normalize_phone("09876543210") == canonical
    assert normalize_phone("91-98765-43210") == canonical
    assert normalize_phone("(+91) 9876543210") == canonical
    assert normalize_phone("+919876543210") == canonical


def test_phone_rejects_short_bad_prefix_landline_and_foreign():
    assert normalize_phone("987654321") is None
    assert normalize_phone("5876543210") is None
    assert normalize_phone("022-2345-6789") is None
    assert normalize_phone("N/A") is None
    assert normalize_phone("+1 415 555 0134") is None


def test_dates_are_day_first_and_reject_impossible_days():
    assert parse_date("2026-12-01") == date(2026, 12, 1)
    assert parse_date("01/12/2026") == date(2026, 12, 1)
    assert parse_date("01-12-2026") == date(2026, 12, 1)
    assert parse_date("01-Dec-2026") == date(2026, 12, 1)
    assert parse_date("01-dec-2026") == date(2026, 12, 1)
    assert parse_date("31/02/2026") is None
    assert parse_date("2026-13-01") is None
    assert parse_date("soon") is None


def test_decimal_prefixes_commas_and_half_up():
    assert parse_decimal("12500") == Decimal("12500.00")
    assert parse_decimal("₹12,500") == Decimal("12500.00")
    assert parse_decimal("Rs. 1200") == Decimal("1200.00")
    assert parse_decimal("INR 10.1") == Decimal("10.10")
    assert parse_decimal("1,234.567") == Decimal("1234.57")
    assert parse_decimal("0.005") == Decimal("0.01")
    assert parse_decimal("-1") is None
    assert parse_decimal("twelve thousand") is None
    assert parse_decimal("N/A") is None


def test_enum_is_case_insensitive_and_stored_lower():
    allowed = ["en", "hi", "ta"]
    assert parse_enum(" HI ", allowed) == "hi"
    assert parse_enum("tamil", allowed) is None
    assert parse_enum("Hindi", allowed) is None


def test_timestamps_utc_offset_and_naive_ist():
    utc = parse_timestamp("2026-10-05T07:50:21Z")
    offset = parse_timestamp("2026-10-05T13:20:21+05:30")
    naive = parse_timestamp("2026-10-03 10:01:00")
    assert utc == datetime(2026, 10, 5, 7, 50, 21, tzinfo=timezone.utc)
    assert offset == utc
    assert naive.tzinfo == IST
    assert format_ist(naive) == "2026-10-03T10:01:00+05:30"
    assert format_ist(utc) == "2026-10-05T13:20:21+05:30"
