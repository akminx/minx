from __future__ import annotations

import json
from pathlib import Path

from minx_mcp.db import get_connection
from scripts.scan_memory_for_secrets import main


def _fake_github_token() -> str:
    return "".join(("gh", "p_", "a" * 36))


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "scan.db"
    get_connection(db_path).close()
    return db_path


def _insert_memory(db_path: Path, *, subject: str = "safe", payload: dict[str, object] | str = "{}") -> int:
    conn = get_connection(db_path)
    try:
        payload_json = payload if isinstance(payload, str) else json.dumps(payload)
        cur = conn.execute(
            """
            INSERT INTO memories (
                memory_type, scope, subject, confidence, status,
                payload_json, source, reason, created_at, updated_at
            ) VALUES ('preference', 'core', ?, 0.9, 'active', ?, 'user', '', datetime('now'), datetime('now'))
            """,
            (subject, payload_json),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_scan_memory_for_secrets_clean_db_exits_zero(tmp_path: Path, capsys) -> None:
    db_path = _fresh_db(tmp_path)
    _insert_memory(db_path, payload={"value": "safe"})

    rc = main([str(db_path)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "findings=0" in captured.out


def test_scan_memory_for_secrets_reports_memory_field_without_raw_secret(tmp_path: Path, capsys) -> None:
    db_path = _fresh_db(tmp_path)
    secret = _fake_github_token()
    row_id = _insert_memory(db_path, payload={"value": secret})

    rc = main([str(db_path)])

    captured = capsys.readouterr()
    assert rc == 2
    assert f"memory id={row_id} field=payload.value kind=github_token" in captured.out
    assert secret not in captured.out


def test_scan_memory_for_secrets_reports_event_payload_without_raw_secret(tmp_path: Path, capsys) -> None:
    db_path = _fresh_db(tmp_path)
    secret = _fake_github_token()
    memory_id = _insert_memory(db_path, payload={"value": "safe"})
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO memory_events (memory_id, event_type, payload_json, actor, created_at)
            VALUES (?, 'payload_updated', ?, 'user', datetime('now'))
            """,
            (memory_id, json.dumps({"payload": {"value": secret}})),
        )
        conn.commit()
        event_id = int(cur.lastrowid)
    finally:
        conn.close()

    rc = main([str(db_path)])

    captured = capsys.readouterr()
    assert rc == 2
    assert f"event id={event_id} field=payload.value kind=github_token" in captured.out
    assert secret not in captured.out


def test_scan_memory_for_secrets_malformed_payload_reports_failure_and_does_not_mutate(tmp_path: Path, capsys) -> None:
    db_path = _fresh_db(tmp_path)
    row_id = _insert_memory(db_path, payload="{not-json")

    rc = main([str(db_path)])

    captured = capsys.readouterr()
    assert rc == 2
    assert f"memory id={row_id} field=payload_json kind=malformed_json" in captured.out
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT payload_json FROM memories WHERE id = ?", (row_id,)).fetchone()
    finally:
        conn.close()
    assert row["payload_json"] == "{not-json"


def test_scan_memory_for_secrets_missing_db_path_does_not_create_file(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "missing.db"

    rc = main([str(db_path)])

    captured = capsys.readouterr()
    assert rc == 2
    assert "database_not_found" in captured.out
    assert not db_path.exists()
