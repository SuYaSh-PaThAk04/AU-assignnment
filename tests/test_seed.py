"""Full-generator checks. These need Postgres and are slower than the unit tests."""

import subprocess
import sys
from pathlib import Path

import pytest

from solution import REPO_ROOT
from solution.catalog import load_dispositions, load_schemas
from solution.db import DEFAULT_URL, connect, drop_schema
from solution.ingest import ingest_path
from solution.reports import write_reports

SCHEMAS = load_schemas()
DISPOSITIONS = load_dispositions()


def _generate(seed: int, out: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "assignment-a-data/tools/generate_data.py",
            "--seed",
            str(seed),
            "--scale",
            "small",
            "--out",
            str(out),
        ],
        check=True,
        cwd=REPO_ROOT,
    )


def _reports(conn, source: Path, out: Path) -> dict[str, str]:
    ingest_path(conn, source, SCHEMAS, DISPOSITIONS)
    write_reports(conn, out, DISPOSITIONS)
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(out.iterdir())}


@pytest.mark.parametrize("seed", [42, 7])
def test_seed_is_idempotent_and_order_independent(ledger_conn, tmp_path: Path, seed: int):
    data = tmp_path / f"data_{seed}"
    _generate(seed, data)
    sequential = _reports(ledger_conn, data / "drop_1", tmp_path / "partial")
    # partial was drop_1 only; ingest drop_2 and rewrite the sequential report
    ingest_path(ledger_conn, data / "drop_2", SCHEMAS, DISPOSITIONS)
    sequential_out = tmp_path / "sequential"
    write_reports(ledger_conn, sequential_out, DISPOSITIONS)
    sequential = {
        path.name: path.read_text(encoding="utf-8") for path in sorted(sequential_out.iterdir())
    }
    rerun = tmp_path / "rerun"
    ingest_path(ledger_conn, data, SCHEMAS, DISPOSITIONS)
    write_reports(ledger_conn, rerun, DISPOSITIONS)
    rerun_files = {path.name: path.read_text(encoding="utf-8") for path in sorted(rerun.iterdir())}
    assert rerun_files == sequential

    other_schema = ledger_conn.execute("SELECT current_schema() AS s").fetchone()["s"] + "b"
    other = connect(DEFAULT_URL, other_schema)
    try:
        parent = _reports(other, data, tmp_path / "parent")
        assert parent == sequential
        leaked = other.execute(
            """
            SELECT COUNT(*) AS n
            FROM leads
            WHERE customer_name_enc NOT LIKE 'gAAAA%'
               OR attributes ? 'phone'
               OR attributes ? 'customer_name'
               OR phone_hmac LIKE '+91%'
            """
        ).fetchone()["n"]
        assert leaked == 0
    finally:
        other.rollback()
        drop_schema(other, other_schema)
        other.close()
