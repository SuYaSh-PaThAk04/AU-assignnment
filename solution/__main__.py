"""CLI: python -m solution ingest <drop> | report --out <dir>."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from solution.catalog import load_dispositions, load_schemas
from solution.db import DEFAULT_SCHEMA, DEFAULT_URL, connect
from solution.ingest import ingest_path, relink
from solution.reports import write_reports


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_URL),
        help=f"Postgres URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--schema",
        default=os.environ.get("LEDGER_SCHEMA", DEFAULT_SCHEMA),
        help=f"Postgres schema (default: {DEFAULT_SCHEMA})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m solution")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest a drop directory, or a parent of drop_* folders")
    _add_connection_args(ingest)
    ingest.add_argument("path", type=Path)

    report = sub.add_parser("report", help="Write the five report files")
    _add_connection_args(report)
    report.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        conn = connect(args.database_url, args.schema)
    except Exception as exc:
        print(
            "Could not connect to Postgres. Start it with `docker compose up -d` "
            f"and check --database-url.\n{exc}",
            file=sys.stderr,
        )
        return 1
    try:
        schemas = load_schemas()
        dispositions = load_dispositions()
        if args.command == "ingest":
            ingest_path(conn, args.path, schemas, dispositions)
            counts = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM leads) AS leads,
                    (SELECT COUNT(*) FROM events) AS events,
                    (SELECT COUNT(*) FROM events WHERE lead_id IS NULL) AS orphans
                """
            ).fetchone()
            print(
                f"ingested {args.path} schema={args.schema} "
                f"leads={counts['leads']} events={counts['events']} orphans={counts['orphans']}"
            )
        else:
            relink(conn, dispositions)
            write_reports(conn, args.out, dispositions)
            print(f"wrote reports to {args.out}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
