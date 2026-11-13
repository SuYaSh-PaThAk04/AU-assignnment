import json
from pathlib import Path

from solution.catalog import load_dispositions, load_schemas
from solution.ingest import ingest_path
from solution.reports import write_reports

SCHEMAS = load_schemas()
DISPOSITIONS = load_dispositions()


def _write_fixture(root: Path) -> None:
    leads1 = root / "drop_1" / "leads" / "motor_renewal"
    leads2 = root / "drop_1" / "leads" / "motor_renewal"
    alias = root / "drop_2" / "leads" / "motor_renewal"
    events = root / "drop_1" / "events"
    for directory in (leads1, alias, events):
        directory.mkdir(parents=True, exist_ok=True)
    (leads1 / "batch_001.csv").write_text(
        "\n".join(
            [
                "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language",
                "B Lead,9123456780,MOT/2,31/02/2026,1000,en",
                ",9876500000,MOT/X,2026-01-01,10,en",
                "C Bad,12345,MOT/Y,2026-01-01,10,en",
                "D Name,9234567890,MOT/3,2026-01-02,10,en",
                "E Name,9345678901,MOT/4,2026-01-03,10,tamil",
                "F Name,,MOT/Z,2026-01-01,10,en",
                ",,,,,",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (leads2 / "batch_002.csv").write_text(
        "\n".join(
            [
                "customer_name,phone,policy_number,policy_expiry,premium_amount,preferred_language",
                'B Lead,09123456780,MOT/2,2026-02-02,"Rs. 2,500",HI',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (alias / "batch_003.csv").write_text(
        "\n".join(
            [
                "Language,Insured Name,Policy No,Mobile No,Expiry Date,Premium,agent_notes",
                'HI,A Lead,MOT/1,9876543210,01-Dec-2026,"₹12,500",VIP',
                "",
            ]
        ),
        encoding="utf-8",
    )
    lines = [
        '{"event_id":"evt_a_init","event_type":"call.initiated","workflow_id":"motor_renewal","call_id":"call_a","occurred_at":"2026-10-03 10:01:00","phone":"9876543210","payload":{}}',
        '{"event_id":"evt_a_conn","event_type":"call.connected","workflow_id":"motor_renewal","call_id":"call_a","occurred_at":"2026-10-03T04:31:10Z","phone":"+91 98765 43210","payload":{}}',
        '{"event_id":"evt_a_set","event_type":"disposition.set","workflow_id":"motor_renewal","call_id":"call_a","occurred_at":"2026-10-03T10:05:00+05:30","phone":"919876543210","payload":{"disposition_code":"payment_link_sent"}}',
        '{"event_id":"evt_a_over","event_type":"disposition.override","workflow_id":"motor_renewal","call_id":"call_a","occurred_at":"2026-10-04T10:00:00+05:30","payload":{"disposition_code":"renewed","overridden_by":"s","reason":"qa"}}',
        '{"event_id":"evt_a_init","event_type":"call.initiated","workflow_id":"motor_renewal","call_id":"call_a","occurred_at":"2026-10-03 10:01:00","phone":"9876543210","payload":{}}',
        "{not-json",
        '{"event_id":"evt_a_bad","event_type":"disposition.set","workflow_id":"motor_renewal","call_id":"call_a","occurred_at":"2026-10-03T10:06:00+05:30","phone":"9876543210","payload":{"disposition_code":"RENEWD"}}',
        '{"event_id":"evt_orphan","event_type":"call.initiated","workflow_id":"motor_renewal","call_id":"call_o","occurred_at":"2026-10-03 09:00:00","phone":"9000000001","payload":{}}',
        '{"event_id":"evt_d_init","event_type":"call.initiated","workflow_id":"motor_renewal","call_id":"call_d","occurred_at":"2026-10-04 11:00:00","phone":"9234567890","payload":{}}',
        '{"event_id":"evt_d_fail","event_type":"call.telephony_failed","workflow_id":"motor_renewal","call_id":"call_d","occurred_at":"2026-10-04 11:00:05","phone":"9234567890","payload":{"reason":"sip_503"}}',
        '{"event_id":"evt_e_init","event_type":"call.initiated","workflow_id":"motor_renewal","call_id":"call_e","occurred_at":"2026-10-04 12:00:00","phone":"9345678901","payload":{}}',
        '{"event_id":"evt_e_end","event_type":"call.ended","workflow_id":"motor_renewal","call_id":"call_e","occurred_at":"2026-10-04 12:00:30","phone":"9345678901","payload":{"duration_sec":0,"end_reason":"no_answer"}}',
    ]
    (events / "events_0001.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_reports(out: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(out.iterdir())}


def test_fixture_reports_relink_and_idempotency(ledger_conn, tmp_path: Path):
    root = tmp_path / "data"
    _write_fixture(root)
    out_partial = tmp_path / "partial"
    ingest_path(ledger_conn, root / "drop_1", SCHEMAS, DISPOSITIONS)
    write_reports(ledger_conn, out_partial, DISPOSITIONS)
    partial = json.loads((out_partial / "data_quality.json").read_text(encoding="utf-8"))
    assert partial["orphan_events"] == 6
    assert partial["unknown_disposition_events"] == 0

    out = tmp_path / "reports"
    ingest_path(ledger_conn, root / "drop_2", SCHEMAS, DISPOSITIONS)
    write_reports(ledger_conn, out, DISPOSITIONS)

    quality = json.loads((out / "data_quality.json").read_text(encoding="utf-8"))
    assert quality == {
        "lead_rows_total": 8,
        "lead_rows_rejected": 3,
        "rejections_by_reason": {
            "invalid_phone": 1,
            "missing_required:customer_name": 1,
            "missing_required:phone": 1,
        },
        "duplicate_lead_rows_merged": 1,
        "field_warnings": 2,
        "events_total": 12,
        "malformed_events": 1,
        "duplicate_events_dropped": 1,
        "orphan_events": 1,
        "unknown_disposition_events": 1,
    }
    assert (out / "batch_summary.csv").read_text(encoding="utf-8") == (
        "workflow_id,batch_id,total_leads,yet_to_reach_out,in_progress_telephony_failed,in_progress_dialed,completed\n"
        "motor_renewal,batch_001,3,1,1,1,0\n"
        "motor_renewal,batch_003,1,0,0,0,1\n"
    )
    assert (out / "daily_overview.csv").read_text(encoding="utf-8") == (
        "workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads\n"
        "motor_renewal,2026-10-03,1,1,1.0000,1\n"
        "motor_renewal,2026-10-04,2,0,0.0000,0\n"
    )
    lead_report = (out / "lead_report.csv").read_text(encoding="utf-8")
    assert "MOT/1,batch_003,+91******3210,completed,1,1,2026-10-03T10:01:00+05:30,renewed,true" in lead_report
    assert "MOT/2,batch_001,+91******6780,yet_to_reach_out,0,0,,,false" in lead_report
    assert "MOT/3,batch_001,+91******7890,in_progress_telephony_failed,1,0,2026-10-04T11:00:00+05:30,,false" in lead_report
    assert "MOT/4,batch_001,+91******8901,in_progress_dialed,1,0,2026-10-04T12:00:00+05:30,,false" in lead_report
    assert (out / "rejections.csv").read_text(encoding="utf-8") == (
        "workflow_id,batch_id,row_number,reason\n"
        "motor_renewal,batch_001,2,missing_required:customer_name\n"
        "motor_renewal,batch_001,3,invalid_phone\n"
        "motor_renewal,batch_001,6,missing_required:phone\n"
    )

    merged = ledger_conn.execute(
        """
        SELECT batch_id, attributes, customer_name_enc
        FROM leads
        WHERE external_ref = 'MOT/2'
        """
    ).fetchone()
    assert merged["batch_id"] == "batch_001"
    assert merged["attributes"]["premium_amount"] == "2500.00"
    assert merged["attributes"]["policy_expiry"] == "2026-02-02"
    assert merged["attributes"]["preferred_language"] == "hi"
    assert "B Lead" not in merged["customer_name_enc"]
    assert merged["customer_name_enc"].startswith("gAAAA")
    lead_e = ledger_conn.execute(
        "SELECT attributes FROM leads WHERE external_ref = 'MOT/4'"
    ).fetchone()
    assert lead_e["attributes"]["preferred_language"] == "en"
    lead_a = ledger_conn.execute(
        "SELECT attributes FROM leads WHERE external_ref = 'MOT/1'"
    ).fetchone()
    assert lead_a["attributes"]["premium_amount"] == "12500.00"
    assert lead_a["attributes"]["policy_expiry"] == "2026-12-01"
    assert lead_a["attributes"]["preferred_language"] == "hi"

    first = _read_reports(out)
    ingest_path(ledger_conn, root, SCHEMAS, DISPOSITIONS)
    again = tmp_path / "again"
    write_reports(ledger_conn, again, DISPOSITIONS)
    assert _read_reports(again) == first


def test_parent_directory_matches_drop_by_drop(ledger_conn, tmp_path: Path):
    root = tmp_path / "data"
    _write_fixture(root)
    sequential = tmp_path / "sequential"
    ingest_path(ledger_conn, root / "drop_1", SCHEMAS, DISPOSITIONS)
    ingest_path(ledger_conn, root / "drop_2", SCHEMAS, DISPOSITIONS)
    write_reports(ledger_conn, sequential, DISPOSITIONS)

    from solution.db import DEFAULT_URL, connect, drop_schema

    other_schema = ledger_conn.execute("SELECT current_schema() AS s").fetchone()["s"] + "_b"
    other = connect(DEFAULT_URL, other_schema)
    try:
        parent_out = tmp_path / "parent"
        ingest_path(other, root, SCHEMAS, DISPOSITIONS)
        write_reports(other, parent_out, DISPOSITIONS)
        assert _read_reports(parent_out) == _read_reports(sequential)
    finally:
        other.rollback()
        drop_schema(other, other_schema)
        other.close()
