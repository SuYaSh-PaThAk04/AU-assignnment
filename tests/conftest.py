import uuid

import psycopg
import pytest

from solution.db import DEFAULT_URL, connect, drop_schema


@pytest.fixture
def ledger_conn():
    schema = "t_" + uuid.uuid4().hex[:12]
    try:
        conn = connect(DEFAULT_URL, schema)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres is not available: {exc}")
    try:
        yield conn
    finally:
        conn.rollback()
        drop_schema(conn, schema)
        conn.close()
