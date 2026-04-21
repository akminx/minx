from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from minx_mcp.db import get_connection


def test_smoke_script_is_non_destructive_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    completed = subprocess.run(  # noqa: S603 - integration test runs repo smoke script with fixed argv
        [
            sys.executable,
            "scripts/hermes_slice4_smoke.py",
            "--db-path",
            str(db_path),
            "--review-date",
            "2026-05-01",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["date"] == "2026-05-01"
    assert payload["in_place"] is False
    assert payload["used_temporary_copy"] is True
    assert payload["source_db_path"] == str(db_path.resolve())
    assert payload["run_db_path"] is None
    assert payload["training"]["last_session_at"].startswith("2026-05-01")

    conn = get_connection(db_path)
    try:
        session_count = conn.execute("SELECT COUNT(*) FROM training_sessions").fetchone()[0]
        meal_count = conn.execute("SELECT COUNT(*) FROM meals_meal_entries").fetchone()[0]
    finally:
        conn.close()
    assert session_count == 0
    assert meal_count == 0


def test_smoke_script_in_place_writes_seed_data(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    completed = subprocess.run(  # noqa: S603 - integration test runs repo smoke script with fixed argv
        [
            sys.executable,
            "scripts/hermes_slice4_smoke.py",
            "--db-path",
            str(db_path),
            "--review-date",
            "2026-05-02",
            "--in-place",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["in_place"] is True
    assert payload["used_temporary_copy"] is False
    assert payload["run_db_path"] == str(db_path.resolve())

    conn = get_connection(db_path)
    try:
        session_count = conn.execute("SELECT COUNT(*) FROM training_sessions").fetchone()[0]
        meal_count = conn.execute("SELECT COUNT(*) FROM meals_meal_entries").fetchone()[0]
    finally:
        conn.close()
    assert session_count == 2
    assert meal_count == 1


def test_smoke_script_rejects_invalid_review_date(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    completed = subprocess.run(  # noqa: S603 - integration test runs repo smoke script with fixed argv
        [
            sys.executable,
            "scripts/hermes_slice4_smoke.py",
            "--db-path",
            str(db_path),
            "--review-date",
            "not-a-date",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "--review-date must be a valid ISO date" in completed.stderr
