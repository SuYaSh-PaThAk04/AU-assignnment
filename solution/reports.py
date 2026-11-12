"""Derive call facts and write the five report files."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import psycopg

from solution.normalize import format_ist, to_ist

BATCH_HEADER = [
    "workflow_id",
    "batch_id",
    "total_leads",
    "yet_to_reach_out",
    "in_progress_telephony_failed",
    "in_progress_dialed",
    "completed",
]
DAILY_HEADER = [
    "workflow_id",
    "date",
    "total_calls",
    "connected_calls",
    "connectivity_rate",
    "success_leads",
]
LEAD_HEADER = [
    "workflow_id",
    "external_ref",
    "batch_id",
    "phone_masked",
    "bucket",
    "total_calls",
    "connected_calls",
    "last_call_at",
    "current_disposition",
    "ever_success",
]
REJECTION_HEADER = ["workflow_id", "batch_id", "row_number", "reason"]


@dataclass
class CallFact:
    call_id: str
    initiated_at: datetime
    connected: bool
    telephony_failed: bool
    disposition: str | None


def mask_phone(last4: str) -> str:
    return "+91******" + last4


def format_rate(connected: int, total: int) -> str:
    quantized = (Decimal(connected) / Decimal(total)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    return f"{quantized:.4f}"


def effective_disposition(events: list[dict]) -> str | None:
    """Latest override wins over every set. Tie-break on event_id."""
    usable = [
        event
        for event in events
        if event["event_type"] in ("disposition.set", "disposition.override")
        and not event.get("ignored_reason")
    ]
    overrides = [event for event in usable if event["event_type"] == "disposition.override"]
    pool = overrides or [event for event in usable if event["event_type"] == "disposition.set"]
    if not pool:
        return None
    chosen = max(pool, key=lambda event: (event["occurred_at"], event["event_id"]))
    payload = chosen.get("payload") or {}
    code = payload.get("disposition_code")
    if not isinstance(code, str) or not code:
        return None
    return code


def build_calls(events: list[dict]) -> list[CallFact]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        call_id = event.get("call_id")
        if call_id:
            grouped[call_id].append(event)
    calls: list[CallFact] = []
    for call_id, call_events in grouped.items():
        initiated = [event for event in call_events if event["event_type"] == "call.initiated"]
        if not initiated:
            continue
        initiated.sort(key=lambda event: (event["occurred_at"], event["event_id"]))
        calls.append(
            CallFact(
                call_id=call_id,
                initiated_at=initiated[0]["occurred_at"],
                connected=any(event["event_type"] == "call.connected" for event in call_events),
                telephony_failed=any(
                    event["event_type"] == "call.telephony_failed" for event in call_events
                ),
                disposition=effective_disposition(call_events),
            )
        )
    calls.sort(key=lambda call: (call.initiated_at, call.call_id))
    return calls


def lead_bucket(calls: list[CallFact], catalogue: dict[str, dict]) -> str:
    if not calls:
        return "yet_to_reach_out"

    def is_terminal(call: CallFact) -> bool:
        if not call.disposition:
            return False
        info = catalogue.get(call.disposition)
        return bool(info and info["is_terminal"])

    if any(is_terminal(call) for call in calls):
        return "completed"
    if all(call.telephony_failed for call in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def _is_success(code: str | None, catalogue: dict[str, dict]) -> bool:
    if not code:
        return False
    info = catalogue.get(code)
    return bool(info and info["is_success"])


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _quality(conn: psycopg.Connection) -> dict:
    def scalar(query: str) -> int:
        return int(conn.execute(query).fetchone()["n"])

    reason_rows = conn.execute(
        """
        SELECT reason, COUNT(*) AS n
        FROM lead_rows
        WHERE status = 'rejected'
        GROUP BY reason
        ORDER BY reason
        """
    ).fetchall()
    warnings = conn.execute(
        "SELECT COALESCE(SUM(field_warnings), 0) AS n FROM lead_rows"
    ).fetchone()["n"]
    return {
        "lead_rows_total": scalar("SELECT COUNT(*) AS n FROM lead_rows"),
        "lead_rows_rejected": scalar(
            "SELECT COUNT(*) AS n FROM lead_rows WHERE status = 'rejected'"
        ),
        "rejections_by_reason": {row["reason"]: int(row["n"]) for row in reason_rows},
        "duplicate_lead_rows_merged": scalar(
            "SELECT COUNT(*) AS n FROM lead_rows WHERE status = 'merged'"
        ),
        "field_warnings": int(warnings),
        "events_total": scalar("SELECT COUNT(*) AS n FROM event_lines"),
        "malformed_events": scalar(
            "SELECT COUNT(*) AS n FROM event_lines WHERE kind = 'malformed'"
        ),
        "duplicate_events_dropped": scalar(
            "SELECT COUNT(*) AS n FROM event_lines WHERE kind = 'duplicate'"
        ),
        "orphan_events": scalar("SELECT COUNT(*) AS n FROM events WHERE lead_id IS NULL"),
        "unknown_disposition_events": scalar(
            """
            SELECT COUNT(*) AS n FROM events
            WHERE ignored_reason = 'unknown_disposition'
            """
        ),
    }


def write_reports(
    conn: psycopg.Connection,
    out_dir: Path,
    dispositions: dict[str, dict[str, dict]],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    leads = conn.execute(
        """
        SELECT lead_id, workflow_id, phone_last4, batch_id, external_ref
        FROM leads
        ORDER BY lead_id
        """
    ).fetchall()
    events = conn.execute(
        """
        SELECT event_id, event_type, workflow_id, call_id, occurred_at,
               payload, lead_id, ignored_reason
        FROM events
        WHERE lead_id IS NOT NULL
        """
    ).fetchall()
    by_lead: dict[int, list[dict]] = defaultdict(list)
    for event in events:
        by_lead[event["lead_id"]].append(event)

    calls_by_lead: dict[int, list[CallFact]] = {}
    buckets: dict[int, str] = {}
    for lead in leads:
        catalogue = dispositions.get(lead["workflow_id"], {})
        calls = build_calls(by_lead.get(lead["lead_id"], []))
        calls_by_lead[lead["lead_id"]] = calls
        buckets[lead["lead_id"]] = lead_bucket(calls, catalogue)

    batch_counts: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {
            "total_leads": 0,
            "yet_to_reach_out": 0,
            "in_progress_telephony_failed": 0,
            "in_progress_dialed": 0,
            "completed": 0,
        }
    )
    for lead in leads:
        slot = batch_counts[(lead["workflow_id"], lead["batch_id"])]
        slot["total_leads"] += 1
        slot[buckets[lead["lead_id"]]] += 1
    batch_rows = []
    for (workflow_id, batch_id) in sorted(batch_counts):
        slot = batch_counts[(workflow_id, batch_id)]
        batch_rows.append(
            [
                workflow_id,
                batch_id,
                slot["total_leads"],
                slot["yet_to_reach_out"],
                slot["in_progress_telephony_failed"],
                slot["in_progress_dialed"],
                slot["completed"],
            ]
        )
    _write_csv(out_dir / "batch_summary.csv", BATCH_HEADER, batch_rows)

    daily: dict[tuple[str, str], dict] = {}
    for lead in leads:
        catalogue = dispositions.get(lead["workflow_id"], {})
        for call in calls_by_lead[lead["lead_id"]]:
            day = to_ist(call.initiated_at).date().isoformat()
            key = (lead["workflow_id"], day)
            slot = daily.get(key)
            if slot is None:
                slot = {
                    "total_calls": 0,
                    "connected_calls": 0,
                    "success_leads": set(),
                }
                daily[key] = slot
            slot["total_calls"] += 1
            if call.connected:
                slot["connected_calls"] += 1
            if _is_success(call.disposition, catalogue):
                slot["success_leads"].add(lead["lead_id"])
    daily_rows = []
    for workflow_id, day in sorted(daily):
        slot = daily[(workflow_id, day)]
        daily_rows.append(
            [
                workflow_id,
                day,
                slot["total_calls"],
                slot["connected_calls"],
                format_rate(slot["connected_calls"], slot["total_calls"]),
                len(slot["success_leads"]),
            ]
        )
    _write_csv(out_dir / "daily_overview.csv", DAILY_HEADER, daily_rows)

    lead_rows = []
    for lead in leads:
        catalogue = dispositions.get(lead["workflow_id"], {})
        calls = calls_by_lead[lead["lead_id"]]
        last_call_at = format_ist(calls[-1].initiated_at) if calls else ""
        current = ""
        for call in reversed(calls):
            if call.disposition:
                current = call.disposition
                break
        ever_success = any(_is_success(call.disposition, catalogue) for call in calls)
        lead_rows.append(
            [
                lead["workflow_id"],
                lead["external_ref"] or "",
                lead["batch_id"],
                mask_phone(lead["phone_last4"]),
                buckets[lead["lead_id"]],
                len(calls),
                sum(1 for call in calls if call.connected),
                last_call_at,
                current,
                "true" if ever_success else "false",
            ]
        )
    lead_rows.sort(key=lambda row: (row[0], row[1], row[3]))
    _write_csv(out_dir / "lead_report.csv", LEAD_HEADER, lead_rows)

    rejections = conn.execute(
        """
        SELECT workflow_id, batch_id, row_number, reason
        FROM lead_rows
        WHERE status = 'rejected'
        ORDER BY workflow_id, batch_id, row_number
        """
    ).fetchall()
    _write_csv(
        out_dir / "rejections.csv",
        REJECTION_HEADER,
        [
            [row["workflow_id"], row["batch_id"], row["row_number"], row["reason"]]
            for row in rejections
        ],
    )

    quality = _quality(conn)
    (out_dir / "data_quality.json").write_text(
        json.dumps(quality, indent=2) + "\n",
        encoding="utf-8",
    )
