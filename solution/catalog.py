"""Workflow lead schemas and the disposition catalogue."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from solution import SCHEMA_DIR


def load_schemas(schema_dir: Path | None = None) -> dict[str, dict]:
    directory = schema_dir or SCHEMA_DIR
    schemas: dict[str, dict] = {}
    for path in sorted(directory.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        schemas[document["workflow_id"]] = document
    if not schemas:
        raise FileNotFoundError(f"no workflow schemas in {directory}")
    return schemas


def load_dispositions(schema_dir: Path | None = None) -> dict[str, dict[str, dict]]:
    directory = schema_dir or SCHEMA_DIR
    path = directory / "dispositions.csv"
    catalogue: dict[str, dict[str, dict]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            workflow = catalogue.setdefault(row["workflow_id"], {})
            workflow[row["code"]] = {
                "label": row["label"],
                "is_terminal": row["is_terminal"].strip().lower() == "true",
                "is_success": row["is_success"].strip().lower() == "true",
            }
    return catalogue
