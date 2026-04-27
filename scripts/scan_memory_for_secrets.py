#!/usr/bin/env python
"""Read-only scan for historical secret-shaped memory data (Slice 6h).

Run from the repository root:

    python scripts/scan_memory_for_secrets.py [DB_PATH]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from sqlite3 import Connection

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minx_mcp.config import get_settings
from minx_mcp.core.secret_scanner import scan_for_secrets


def _scan_text(findings: list[tuple[str, int, str, str]], *, surface: str, row_id: int, field: str, text: str) -> None:
    verdict = scan_for_secrets(text)
    findings.extend((surface, row_id, field, finding.kind) for finding in verdict.findings)


def _scan_object(
    findings: list[tuple[str, int, str, str]],
    *,
    surface: str,
    row_id: int,
    field: str,
    value: object,
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            key_field = f"{field}.{key_text}" if field else key_text
            key_verdict = scan_for_secrets(key_text)
            if key_verdict.findings:
                key_field = f"{field}.[REDACTED_KEY]" if field else "[REDACTED_KEY]"
                findings.extend((surface, row_id, key_field, finding.kind) for finding in key_verdict.findings)
            _scan_object(findings, surface=surface, row_id=row_id, field=key_field, value=item)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_object(findings, surface=surface, row_id=row_id, field=f"{field}[{index}]", value=item)
    elif isinstance(value, str):
        _scan_text(findings, surface=surface, row_id=row_id, field=field, text=value)


def _scan_json_field(
    findings: list[tuple[str, int, str, str]],
    *,
    surface: str,
    row_id: int,
    field: str,
    raw: str,
    root_field: str | None = None,
) -> None:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        findings.append((surface, row_id, field, "malformed_json"))
        return
    _scan_object(
        findings,
        surface=surface,
        row_id=row_id,
        field=field.removesuffix("_json") if root_field is None else root_field,
        value=parsed,
    )


def run_scan(conn: Connection) -> list[tuple[str, int, str, str]]:
    findings: list[tuple[str, int, str, str]] = []
    for row in conn.execute(
        """
        SELECT id, memory_type, scope, subject, source, reason, payload_json
        FROM memories
        ORDER BY id ASC
        """
    ):
        row_id = int(row["id"])
        for field in ("memory_type", "scope", "subject", "source", "reason"):
            _scan_text(findings, surface="memory", row_id=row_id, field=field, text=str(row[field]))
        _scan_json_field(
            findings,
            surface="memory",
            row_id=row_id,
            field="payload_json",
            raw=str(row["payload_json"] or ""),
        )
    for row in conn.execute("SELECT id, payload_json FROM memory_events ORDER BY id ASC"):
        _scan_json_field(
            findings,
            surface="event",
            row_id=int(row["id"]),
            field="payload_json",
            raw=str(row["payload_json"] or ""),
            root_field="",
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan historical memory rows for secret-shaped values.")
    parser.add_argument("db_path", nargs="?", default=None)
    args = parser.parse_args(argv)
    db_path = Path(args.db_path) if args.db_path else get_settings().db_path
    if not db_path.exists():
        print(f"database_not_found path={db_path}")
        return 2
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    try:
        findings = run_scan(conn)
    finally:
        conn.close()

    counts = Counter(kind for _, _, _, kind in findings)
    print(f"findings={len(findings)}")
    for kind, count in sorted(counts.items()):
        print(f"kind={kind} count={count}")
    for surface, row_id, field, kind in findings:
        print(f"{surface} id={row_id} field={field} kind={kind}")
    return 2 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
