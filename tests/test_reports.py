from datetime import datetime, timezone

from solution.normalize import IST
from solution.reports import (
    CallFact,
    build_calls,
    effective_disposition,
    format_rate,
    lead_bucket,
    mask_phone,
)

CATALOGUE = {
    "renewed": {"is_terminal": True, "is_success": True},
    "payment_link_sent": {"is_terminal": False, "is_success": False},
    "not_interested": {"is_terminal": True, "is_success": False},
}


def _event(event_id, event_type, when, code=None, call_id="call_1", ignored=None):
    payload = {} if code is None else {"disposition_code": code}
    return {
        "event_id": event_id,
        "event_type": event_type,
        "call_id": call_id,
        "occurred_at": when,
        "payload": payload,
        "ignored_reason": ignored,
    }


def test_override_wins_even_when_the_set_is_later():
    early = datetime(2026, 10, 3, 10, 0, tzinfo=IST)
    later = datetime(2026, 10, 5, 10, 0, tzinfo=IST)
    events = [
        _event("evt_set", "disposition.set", later, "payment_link_sent"),
        _event("evt_over", "disposition.override", early, "renewed"),
    ]
    assert effective_disposition(events) == "renewed"


def test_latest_override_wins_and_unknown_codes_are_ignored():
    t1 = datetime(2026, 10, 3, 10, 0, tzinfo=IST)
    t2 = datetime(2026, 10, 4, 10, 0, tzinfo=IST)
    events = [
        _event("evt_a", "disposition.override", t1, "not_interested"),
        _event("evt_b", "disposition.override", t2, "renewed"),
        _event("evt_bad", "disposition.set", t2, "RENEWD", ignored="unknown_disposition"),
    ]
    assert effective_disposition(events) == "renewed"


def test_tie_breaks_on_event_id():
    moment = datetime(2026, 10, 3, 10, 0, tzinfo=IST)
    events = [
        _event("evt_0001", "disposition.set", moment, "payment_link_sent"),
        _event("evt_0002", "disposition.set", moment, "renewed"),
    ]
    assert effective_disposition(events) == "renewed"


def test_buckets_follow_the_spec_order():
    moment = datetime(2026, 10, 3, 10, 0, tzinfo=IST)
    assert lead_bucket([], CATALOGUE) == "yet_to_reach_out"
    terminal = CallFact("c1", moment, True, False, "renewed")
    failed = CallFact("c2", moment, False, True, None)
    dialed = CallFact("c3", moment, False, False, "payment_link_sent")
    assert lead_bucket([terminal, failed], CATALOGUE) == "completed"
    assert lead_bucket([failed, failed], CATALOGUE) == "in_progress_telephony_failed"
    assert lead_bucket([failed, dialed], CATALOGUE) == "in_progress_dialed"


def test_a_call_requires_initiated_and_rate_rounds_half_up():
    moment = datetime(2026, 10, 3, 4, 31, tzinfo=timezone.utc)
    events = [
        _event("evt_end", "call.ended", moment, call_id="call_x"),
        _event("evt_init", "call.initiated", moment, call_id="call_x"),
        _event("evt_conn", "call.connected", moment, call_id="call_x"),
    ]
    calls = build_calls(events)
    assert len(calls) == 1
    assert calls[0].connected is True
    assert format_rate(2, 3) == "0.6667"
    assert mask_phone("3210") == "+91******3210"
