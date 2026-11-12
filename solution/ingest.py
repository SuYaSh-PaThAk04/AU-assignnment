"""Lead and event ingestion into the append-only ledger."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from solution.normalize import normalize_phone, norm_header, parse_date, parse_decimal, parse_enum, parse_timestamp
from solution.pii import encrypt_name, phone_hmac

_SKIP_ATTR = ("customer_name", "phone")


@dataclass
class LeadClassification:
    reason: str | None
    warnings: int
    values: dict[str, Any]


def header_lookup(schema: dict) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for field in schema["fields"]:
        lookup[norm_header(field["name"])] = field["name"]
        for alias in field.get("aliases", []):
            lookup[norm_header(alias)] = field["name"]
    return lookup


def project_row(lookup: dict[str, str], row: dict) -> dict[str, str]:
    """Map a CSV row onto schema field names. Unknown columns are ignored."""
    found: dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        field_name = lookup.get(norm_header(str(key)))
        if field_name is None:
            continue
        text = "" if value is None else str(value).strip()
        if field_name not in found or text:
            found[field_name] = text
    return found


def _parse_field(field: dict, text: str) -> Any | None:
    kind = field["type"]
    if kind == "string":
        return text
    if kind == "phone":
        return normalize_phone(text)
    if kind == "date":
        return parse_date(text)
    if kind == "decimal":
        return parse_decimal(text)
    if kind == "enum":
        return parse_enum(text, field.get("values", []))
    return text


def validate_row(schema: dict, raw_by_field: dict[str, str]) -> LeadClassification:
    """Walk fields in schema order. The first failure is the row's only reason."""
    reason: str | None = None
    warnings = 0
    values: dict[str, Any] = {}
    for field in schema["fields"]:
        name = field["name"]
        raw = raw_by_field.get(name)
        if raw is None or raw.strip() == "":
            values[name] = None
            if field.get("required") and reason is None:
                reason = f"missing_required:{name}"
            continue
        parsed = _parse_field(field, raw.strip())
        if parsed is None:
            values[name] = None
            if field["type"] == "phone":
                if reason is None:
                    reason = "invalid_phone"
            elif not field.get("required"):
                warnings += 1
            elif reason is None:
                reason = f"missing_required:{name}"
        else:
            values[name] = parsed
    if reason is not None:
        warnings = 0
    return LeadClassification(reason=reason, warnings=warnings, values=values)


def _reference_field(schema: dict) -> str:
    for field in schema["fields"]:
        if field.get("reference"):
            return field["name"]
    raise ValueError(f"schema {schema['workflow_id']} has no reference field")


def _serialize(field: dict, value: Any) -> Any:
    if value is None:
        return None
    if field["type"] == "date":
        return value.isoformat()
    if field["type"] == "decimal":
        return format(value, "f")
    return value


def _attribute_fields(schema: dict) -> list[dict]:
    reference = _reference_field(schema)
    return [
        field
        for field in schema["fields"]
        if field["name"] not in _SKIP_ATTR and field["name"] != reference
    ]


def _apply_default_attrs(schema: dict, attributes: dict) -> dict:
    updated = dict(attributes)
    for field in _attribute_fields(schema):
        if updated.get(field["name"]) is None and "default" in field:
            updated[field["name"]] = field["default"]
    return updated


def _attributes_from_values(schema: dict, values: dict[str, Any]) -> dict:
    attributes = {}
    for field in _attribute_fields(schema):
        attributes[field["name"]] = _serialize(field, values.get(field["name"]))
    return _apply_default_attrs(schema, attributes)


def _blank_row(row: dict) -> bool:
    return all(value is None or str(value).strip() == "" for value in row.values())


def discover_drops(path: Path) -> list[Path]:
    """A parent directory of drop_* folders is ingested in name order."""
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_dir():
        raise NotADirectoryError(path)
    drop_dirs = sorted(
        child for child in path.iterdir() if child.is_dir() and child.name.startswith("drop_")
    )
    if drop_dirs and not (path / "leads").exists() and not (path / "events").exists():
        return drop_dirs
    return [path]


def _already_ingested(conn: psycopg.Connection, file_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ingest_files WHERE file_key = %s",
        (file_key,),
    ).fetchone()
    return row is not None


def _mark_ingested(conn: psycopg.Connection, file_key: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO ingest_files (file_key, kind) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (file_key, kind),
    )


def ingest_lead_file(
    conn: psycopg.Connection,
    path: Path,
    workflow_id: str,
    schema: dict,
) -> None:
    file_key = str(path.resolve())
    if _already_ingested(conn, file_key):
        return
    lookup = header_lookup(schema)
    reference = _reference_field(schema)
    batch_id = path.stem
    row_number = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            if raw_row is None or _blank_row(raw_row):
                continue
            row_number += 1
            projected = project_row(lookup, raw_row)
            classified = validate_row(schema, projected)
            if classified.reason is not None:
                conn.execute(
                    """
                    INSERT INTO lead_rows (
                        file_key, row_number, workflow_id, batch_id, status, reason, field_warnings
                    ) VALUES (%s, %s, %s, %s, 'rejected', %s, 0)
                    """,
                    (file_key, row_number, workflow_id, batch_id, classified.reason),
                )
                continue
            canonical = classified.values["phone"]
            digest = phone_hmac(canonical)
            existing = conn.execute(
                """
                SELECT lead_id, external_ref, customer_name_enc, attributes
                FROM leads
                WHERE workflow_id = %s AND phone_hmac = %s
                """,
                (workflow_id, digest),
            ).fetchone()
            if existing is None:
                attributes = _attributes_from_values(schema, classified.values)
                name = classified.values.get("customer_name")
                conn.execute(
                    """
                    INSERT INTO leads (
                        workflow_id, phone_hmac, phone_last4, batch_id,
                        external_ref, customer_name_enc, attributes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        workflow_id,
                        digest,
                        canonical[-4:],
                        batch_id,
                        classified.values.get(reference),
                        encrypt_name(name) if name else None,
                        Jsonb(attributes),
                    ),
                )
                status = "created"
            else:
                attributes = dict(existing["attributes"] or {})
                external_ref = existing["external_ref"]
                name_enc = existing["customer_name_enc"]
                for field in schema["fields"]:
                    name = field["name"]
                    if name == "phone":
                        continue
                    incoming = classified.values.get(name)
                    if incoming is None:
                        continue
                    if name == "customer_name":
                        name_enc = encrypt_name(incoming)
                    elif name == reference:
                        external_ref = incoming
                    else:
                        attributes[name] = _serialize(field, incoming)
                attributes = _apply_default_attrs(schema, attributes)
                conn.execute(
                    """
                    UPDATE leads
                    SET external_ref = %s, customer_name_enc = %s, attributes = %s
                    WHERE lead_id = %s
                    """,
                    (external_ref, name_enc, Jsonb(attributes), existing["lead_id"]),
                )
                status = "merged"
            conn.execute(
                """
                INSERT INTO lead_rows (
                    file_key, row_number, workflow_id, batch_id, status, reason, field_warnings
                ) VALUES (%s, %s, %s, %s, %s, NULL, %s)
                """,
                (file_key, row_number, workflow_id, batch_id, status, classified.warnings),
            )
    _mark_ingested(conn, file_key, "leads")
    conn.commit()


def _parse_event_object(line: str) -> dict | None:
    try:
        document = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(document, dict):
        return None
    event_id = document.get("event_id")
    if not isinstance(event_id, str) or not event_id.strip():
        return None
    return document


def ingest_event_file(conn: psycopg.Connection, path: Path) -> None:
    file_key = str(path.resolve())
    if _already_ingested(conn, file_key):
        return
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            document = _parse_event_object(line)
            if document is None:
                conn.execute(
                    """
                    INSERT INTO event_lines (file_key, line_number, kind, event_id)
                    VALUES (%s, %s, 'malformed', NULL)
                    """,
                    (file_key, line_number),
                )
                continue
            event_id = document["event_id"].strip()
            # A timestamp we cannot place is not a usable ledger row. The spec's
            # malformed class is JSON / missing event_id; an unparseable clock is
            # recorded the same way so it cannot be ordered or reported.
            occurred_at = parse_timestamp(str(document.get("occurred_at", "")))
            seen = conn.execute(
                "SELECT 1 FROM events WHERE event_id = %s",
                (event_id,),
            ).fetchone()
            if seen is not None or occurred_at is None:
                kind = "duplicate" if seen is not None else "malformed"
                conn.execute(
                    """
                    INSERT INTO event_lines (file_key, line_number, kind, event_id)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (file_key, line_number, kind, event_id if seen is not None else None),
                )
                continue
            raw_phone = document.get("phone")
            digest = None
            if isinstance(raw_phone, str) and raw_phone.strip():
                canonical = normalize_phone(raw_phone)
                if canonical is not None:
                    digest = phone_hmac(canonical)
            payload = document.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            conn.execute(
                """
                INSERT INTO events (
                    event_id, event_type, workflow_id, call_id, occurred_at,
                    phone_hmac, payload, lead_id, ignored_reason, source_file, source_line
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s)
                """,
                (
                    event_id,
                    str(document.get("event_type") or ""),
                    document.get("workflow_id"),
                    document.get("call_id"),
                    occurred_at,
                    digest,
                    Jsonb(payload),
                    file_key,
                    line_number,
                ),
            )
            conn.execute(
                """
                INSERT INTO event_lines (file_key, line_number, kind, event_id)
                VALUES (%s, %s, 'stored', %s)
                """,
                (file_key, line_number, event_id),
            )
    _mark_ingested(conn, file_key, "events")
    conn.commit()


def relink(conn: psycopg.Connection, dispositions: dict[str, dict[str, dict]]) -> None:
    """Attach orphans whose lead has since arrived, then flag unknown codes."""
    conn.execute(
        """
        UPDATE events AS e
        SET lead_id = l.lead_id
        FROM leads AS l
        WHERE e.lead_id IS NULL
          AND e.phone_hmac IS NOT NULL
          AND e.workflow_id = l.workflow_id
          AND e.phone_hmac = l.phone_hmac
        """
    )
    conn.execute(
        """
        UPDATE events AS e
        SET lead_id = src.lead_id
        FROM (
            SELECT DISTINCT ON (call_id) call_id, lead_id
            FROM events
            WHERE lead_id IS NOT NULL AND call_id IS NOT NULL
            ORDER BY call_id, event_id
        ) AS src
        WHERE e.lead_id IS NULL
          AND e.call_id IS NOT NULL
          AND e.call_id = src.call_id
        """
    )
    for workflow_id, codes in dispositions.items():
        known = list(codes)
        conn.execute(
            """
            UPDATE events
            SET ignored_reason = 'unknown_disposition'
            WHERE lead_id IS NOT NULL
              AND workflow_id = %s
              AND event_type IN ('disposition.set', 'disposition.override')
              AND NOT (COALESCE(payload->>'disposition_code', '') = ANY(%s))
            """,
            (workflow_id, known),
        )
        conn.execute(
            """
            UPDATE events
            SET ignored_reason = NULL
            WHERE workflow_id = %s
              AND event_type IN ('disposition.set', 'disposition.override')
              AND (
                    lead_id IS NULL
                    OR COALESCE(payload->>'disposition_code', '') = ANY(%s)
              )
            """,
            (workflow_id, known),
        )
    known_workflows = list(dispositions)
    conn.execute(
        """
        UPDATE events
        SET ignored_reason = 'unknown_disposition'
        WHERE lead_id IS NOT NULL
          AND event_type IN ('disposition.set', 'disposition.override')
          AND (workflow_id IS NULL OR workflow_id <> ALL(%s))
        """,
        (known_workflows,),
    )
    conn.commit()


def ingest_drop(
    conn: psycopg.Connection,
    drop: Path,
    schemas: dict[str, dict],
) -> None:
    leads_root = drop / "leads"
    if leads_root.is_dir():
        for workflow_dir in sorted(path for path in leads_root.iterdir() if path.is_dir()):
            schema = schemas.get(workflow_dir.name)
            if schema is None:
                raise KeyError(f"no lead schema for workflow {workflow_dir.name}")
            for csv_path in sorted(workflow_dir.glob("*.csv")):
                ingest_lead_file(conn, csv_path, workflow_dir.name, schema)
    events_root = drop / "events"
    if events_root.is_dir():
        for jsonl_path in sorted(events_root.glob("*.jsonl")):
            ingest_event_file(conn, jsonl_path)


def ingest_path(
    conn: psycopg.Connection,
    path: Path,
    schemas: dict[str, dict],
    dispositions: dict[str, dict[str, dict]],
) -> None:
    for drop in discover_drops(path):
        ingest_drop(conn, drop, schemas)
    relink(conn, dispositions)
