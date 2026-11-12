"""Postgres connection and ledger schema."""

from __future__ import annotations

import re

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

DEFAULT_URL = "postgresql://au:au_local_only@localhost:5432/au_assignment"
DEFAULT_SCHEMA = "ledger"
_SCHEMA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

DDL = """
CREATE TABLE IF NOT EXISTS leads (
    lead_id BIGSERIAL PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    phone_hmac TEXT NOT NULL,
    phone_last4 TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    external_ref TEXT,
    customer_name_enc TEXT,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (workflow_id, phone_hmac)
);

CREATE TABLE IF NOT EXISTS lead_rows (
    file_key TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    workflow_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    field_warnings INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_key, row_number)
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    workflow_id TEXT,
    call_id TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    phone_hmac TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    lead_id BIGINT REFERENCES leads (lead_id),
    ignored_reason TEXT,
    source_file TEXT NOT NULL,
    source_line INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS events_lead_id_idx ON events (lead_id);
CREATE INDEX IF NOT EXISTS events_call_id_idx ON events (call_id);
CREATE INDEX IF NOT EXISTS events_phone_idx ON events (workflow_id, phone_hmac);

CREATE TABLE IF NOT EXISTS event_lines (
    file_key TEXT NOT NULL,
    line_number INTEGER NOT NULL,
    kind TEXT NOT NULL,
    event_id TEXT,
    PRIMARY KEY (file_key, line_number)
);

CREATE TABLE IF NOT EXISTS ingest_files (
    file_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def check_schema_name(schema: str) -> str:
    if not _SCHEMA_NAME.fullmatch(schema):
        raise ValueError(f"invalid schema name: {schema!r}")
    return schema


def connect(database_url: str = DEFAULT_URL, schema: str = DEFAULT_SCHEMA) -> psycopg.Connection:
    """Open a connection, create the schema if needed, and select it."""
    check_schema_name(schema)
    conn = psycopg.connect(database_url, row_factory=dict_row)
    conn.execute("SET TIME ZONE 'UTC'")
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    for statement in DDL.split(";"):
        statement = statement.strip()
        if statement:
            conn.execute(statement)
    conn.commit()
    return conn


def drop_schema(conn: psycopg.Connection, schema: str) -> None:
    check_schema_name(schema)
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    conn.commit()
