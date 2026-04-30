from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from logging import LogRecord
from pathlib import Path

from minx_mcp.core import playbooks as playbook_api
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from tests.helpers import MinxTestConfig, get_tool, read_resource_text


def _seed_running_row(
    conn: sqlite3.Connection,
    *,
    playbook_id: str = "daily_review",
    harness: str = "hermes",
    trigger_type: str = "cron",
    trigger_ref: str = "0 21 * * *",
    triggered_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO playbook_runs (
            playbook_id,
            harness,
            triggered_at,
            trigger_type,
            trigger_ref,
            status
        ) VALUES (?, ?, ?, ?, ?, 'running')
        """,
        (playbook_id, harness, triggered_at, trigger_type, trigger_ref),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def _seed_terminal_row(
    conn: sqlite3.Connection,
    *,
    playbook_id: str,
    harness: str,
    trigger_type: str,
    trigger_ref: str,
    status: str,
    triggered_at: str,
    completed_at: str,
    conditions_met: bool,
    action_taken: bool,
    error_message: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO playbook_runs (
            playbook_id,
            harness,
            triggered_at,
            trigger_type,
            trigger_ref,
            status,
            conditions_met,
            action_taken,
            error_message,
            completed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            playbook_id,
            harness,
            triggered_at,
            trigger_type,
            trigger_ref,
            status,
            1 if conditions_met else 0,
            1 if action_taken else 0,
            error_message,
            completed_at,
        ),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


def test_start_and_complete_playbook_run_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))

    start = get_tool(server, "start_playbook_run").fn
    complete = get_tool(server, "complete_playbook_run").fn
    history = get_tool(server, "playbook_history").fn

    started = start("daily_review", "hermes", "cron", "0 21 * * *")
    assert started["success"] is True
    run_id = int(started["data"]["run_id"])

    done = complete(run_id, "succeeded", True, True, '{"ok": true}', None)
    assert done["success"] is True
    assert int(done["data"]["run_id"]) == run_id

    rows = history("daily_review", "hermes", "succeeded", None, 30, 20)
    assert rows["success"] is True
    assert rows["data"]["truncated"] is False
    assert len(rows["data"]["runs"]) == 1
    row = rows["data"]["runs"][0]
    assert row["id"] == run_id
    assert row["status"] == "succeeded"
    assert row["conditions_met"] is True
    assert row["action_taken"] is True
    assert row["result_json"] == {"ok": True}


def test_playbook_registry_includes_enrichment_sweep() -> None:
    payload = playbook_api.playbook_registry_payload()
    playbooks = {item["id"]: item for item in payload["playbooks"]}

    assert "enrichment_sweep" in playbooks
    assert "core.enrichment_sweep" in playbooks["enrichment_sweep"]["required_tools"]
    assert "core.enrichment_status" in playbooks["enrichment_sweep"]["required_tools"]


def test_complete_playbook_run_condition_miss_records_skipped_state(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    start = get_tool(server, "start_playbook_run").fn
    complete = get_tool(server, "complete_playbook_run").fn
    history = get_tool(server, "playbook_history").fn

    run_id = int(start("daily_review", "hermes", "cron", "0 21 * * *")["data"]["run_id"])
    result = complete(run_id, "skipped", False, False, None, None)
    assert result["success"] is True

    rows = history("daily_review", "hermes", "skipped", None, 30, 10)
    assert rows["success"] is True
    assert len(rows["data"]["runs"]) == 1
    row = rows["data"]["runs"][0]
    assert row["id"] == run_id
    assert row["status"] == "skipped"
    assert row["conditions_met"] is False
    assert row["action_taken"] is False
    assert row["error_message"] is None


def test_complete_playbook_run_failure_records_error_message(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    start = get_tool(server, "start_playbook_run").fn
    complete = get_tool(server, "complete_playbook_run").fn
    history = get_tool(server, "playbook_history").fn

    run_id = int(start("wiki_update", "hermes", "event", "after-daily")["data"]["run_id"])
    result = complete(
        run_id,
        "failed",
        True,
        False,
        '{"stage":"render"}',
        "llm timeout",
    )
    assert result["success"] is True

    rows = history("wiki_update", "hermes", "failed", None, 30, 10)
    assert rows["success"] is True
    assert len(rows["data"]["runs"]) == 1
    row = rows["data"]["runs"][0]
    assert row["id"] == run_id
    assert row["status"] == "failed"
    assert row["conditions_met"] is True
    assert row["action_taken"] is False
    assert row["result_json"] == {"stage": "render"}
    assert row["error_message"] == "llm timeout"


def test_start_playbook_run_conflict_for_duplicate_in_flight_with_null_trigger_ref(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    start = get_tool(server, "start_playbook_run").fn

    first = start("memory_review", "hermes", "manual", None)
    assert first["success"] is True

    second = start("memory_review", "hermes", "manual", None)
    assert second["success"] is False
    assert second["error_code"] == "CONFLICT"


def test_log_playbook_run_writes_terminal_row(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    log_run = get_tool(server, "log_playbook_run").fn

    result = log_run(
        "weekly_report",
        "hermes",
        "cron",
        "0 10 * * 1",
        "skipped",
        False,
        False,
        None,
        None,
    )
    assert result["success"] is True
    run_id = int(result["data"]["run_id"])

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM playbook_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert str(row["status"]) == "skipped"
    assert int(row["conditions_met"]) == 0
    assert int(row["action_taken"]) == 0
    assert row["completed_at"] is not None


def test_log_playbook_run_hides_intermediate_running_row_from_other_connections(
    tmp_path: Path,
) -> None:
    # What this test proves: BEGIN IMMEDIATE holds the write lock across both
    # INSERT and UPDATE, so the observer's SELECT (which fires via trace
    # callback *before* commit) reads the pre-transaction snapshot — zero
    # running rows. It does NOT prove that a reader between two separate
    # commits would see a running row (that scenario can't arise because both
    # statements share one transaction). Any refactor that splits the
    # INSERT/UPDATE into separate transactions would need a new threading test.
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    observer = sqlite3.connect(str(db_path))
    running_row_counts: list[int] = []

    def on_statement(sql: str) -> None:
        if "UPDATE playbook_runs" not in sql:
            return
        count = int(
            observer.execute(
                "SELECT COUNT(*) FROM playbook_runs WHERE status = 'running'",
            ).fetchone()[0]
        )
        running_row_counts.append(count)

    conn.set_trace_callback(on_statement)
    try:
        run_id = playbook_api.log_playbook_run(
            conn,
            playbook_id="weekly_report",
            harness="hermes",
            trigger_type="cron",
            trigger_ref="0 10 * * 1",
            status="succeeded",
            conditions_met=True,
            action_taken=True,
            result_json='{"generated": true}',
            error_message=None,
        )
    finally:
        conn.set_trace_callback(None)
        observer.close()
        conn.close()

    assert running_row_counts
    assert running_row_counts == [0]
    check_conn = sqlite3.connect(str(db_path))
    check_conn.row_factory = sqlite3.Row
    try:
        row = check_conn.execute("SELECT status FROM playbook_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        check_conn.close()
    assert row is not None
    assert str(row["status"]) == "succeeded"


def test_playbook_reconcile_crashed_marks_only_stale_running_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    old_ts = (datetime.now(UTC) - timedelta(minutes=61)).isoformat().replace("+00:00", "Z")
    fresh_ts = (datetime.now(UTC) - timedelta(minutes=3)).isoformat().replace("+00:00", "Z")
    stale_id = _seed_running_row(conn, triggered_at=old_ts)
    _seed_running_row(
        conn,
        playbook_id="memory_review",
        trigger_ref="manual-1",
        triggered_at=fresh_ts,
    )
    conn.close()

    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    reconcile = get_tool(server, "playbook_reconcile_crashed").fn
    report = reconcile(15)
    assert report["success"] is True
    assert report["data"]["reconciled"] == 1
    assert report["data"]["run_ids"] == [stale_id]


def test_playbook_reconcile_crashed_is_idempotent_and_skips_terminal_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    old_ts = (datetime.now(UTC) - timedelta(minutes=120)).isoformat().replace("+00:00", "Z")
    fresh_ts = (datetime.now(UTC) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")

    stale_running_id = _seed_running_row(
        conn,
        playbook_id="daily_review",
        trigger_ref="stale-running",
        triggered_at=old_ts,
    )
    _seed_running_row(
        conn,
        playbook_id="daily_review",
        trigger_ref="fresh-running",
        triggered_at=fresh_ts,
    )
    stale_success_id = _seed_terminal_row(
        conn,
        playbook_id="weekly_report",
        harness="hermes",
        trigger_type="cron",
        trigger_ref="weekly-success",
        status="succeeded",
        triggered_at=old_ts,
        completed_at=old_ts,
        conditions_met=True,
        action_taken=True,
    )
    stale_failed_id = _seed_terminal_row(
        conn,
        playbook_id="wiki_update",
        harness="hermes",
        trigger_type="event",
        trigger_ref="weekly-failed",
        status="failed",
        triggered_at=old_ts,
        completed_at=old_ts,
        conditions_met=True,
        action_taken=False,
        error_message="render failed",
    )
    conn.close()

    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    reconcile = get_tool(server, "playbook_reconcile_crashed").fn

    first = reconcile(15)
    assert first["success"] is True
    assert first["data"]["reconciled"] == 1
    assert first["data"]["run_ids"] == [stale_running_id]

    second = reconcile(15)
    assert second["success"] is True
    assert second["data"]["reconciled"] == 0
    assert second["data"]["run_ids"] == []

    check_conn = sqlite3.connect(str(db_path))
    check_conn.row_factory = sqlite3.Row
    try:
        stale_running = check_conn.execute(
            "SELECT status, error_message FROM playbook_runs WHERE id = ?",
            (stale_running_id,),
        ).fetchone()
        assert stale_running is not None
        assert str(stale_running["status"]) == "failed"
        assert str(stale_running["error_message"]) == "harness crash suspected"

        stale_success = check_conn.execute(
            "SELECT status FROM playbook_runs WHERE id = ?",
            (stale_success_id,),
        ).fetchone()
        assert stale_success is not None
        assert str(stale_success["status"]) == "succeeded"

        stale_failed = check_conn.execute(
            "SELECT status, error_message FROM playbook_runs WHERE id = ?",
            (stale_failed_id,),
        ).fetchone()
        assert stale_failed is not None
        assert str(stale_failed["status"]) == "failed"
        assert str(stale_failed["error_message"]) == "render failed"
    finally:
        check_conn.close()


def test_playbook_history_limit_sets_truncated_flag(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    for idx in range(3):
        _seed_running_row(
            conn,
            trigger_ref=f"manual-{idx}",
            triggered_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
    conn.close()

    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    history = get_tool(server, "playbook_history").fn
    rows = history("daily_review", None, None, None, 30, 2)
    assert rows["success"] is True
    assert len(rows["data"]["runs"]) == 2
    assert rows["data"]["truncated"] is True


def test_playbook_history_since_filters_out_older_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    now = datetime.now(UTC)
    old_ts = (now - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    recent_ts = (now - timedelta(hours=4)).isoformat().replace("+00:00", "Z")
    since = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")

    _seed_running_row(
        conn,
        playbook_id="goal_nudge",
        trigger_ref="old-run",
        triggered_at=old_ts,
    )
    recent_id = _seed_running_row(
        conn,
        playbook_id="goal_nudge",
        trigger_ref="recent-run",
        triggered_at=recent_ts,
    )
    conn.close()

    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    history = get_tool(server, "playbook_history").fn
    rows = history("goal_nudge", "hermes", "running", since, 30, 10)
    assert rows["success"] is True
    assert rows["data"]["truncated"] is False
    assert [int(row["id"]) for row in rows["data"]["runs"]] == [recent_id]


def test_playbook_logging_includes_structured_fields(tmp_path: Path, caplog) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    start = get_tool(server, "start_playbook_run").fn
    complete = get_tool(server, "complete_playbook_run").fn
    log_run = get_tool(server, "log_playbook_run").fn

    with caplog.at_level("INFO", logger="minx_mcp.core.playbooks"):
        run_id = int(start("daily_review", "hermes", "cron", "0 21 * * *")["data"]["run_id"])
        complete(run_id, "succeeded", True, True, '{"ok": true}', None)
        log_run(
            "weekly_report",
            "hermes",
            "cron",
            "0 10 * * 1",
            "succeeded",
            True,
            True,
            '{"generated": true}',
            None,
        )

    records = [
        record
        for record in caplog.records
        if record.name == "minx_mcp.core.playbooks"
        and record.message
        in {"playbook run started", "playbook run completed", "playbook run logged"}
    ]
    assert len(records) == 3
    for record in records:
        _assert_structured_playbook_log(record)


def _assert_structured_playbook_log(record: LogRecord) -> None:
    assert isinstance(record.playbook_id, str)
    assert isinstance(record.run_id, int)
    assert isinstance(record.trigger_type, str)
    assert isinstance(record.status, str)
    assert isinstance(record.duration_ms, int)
    assert record.duration_ms >= 0


def test_playbook_registry_resource_is_json_manifest(tmp_path: Path) -> None:
    server = create_core_server(MinxTestConfig(tmp_path / "m.db", tmp_path / "vault"))
    payload = json.loads(asyncio.run(read_resource_text(server, "playbook://registry")))
    assert isinstance(payload, dict)
    assert "playbooks" in payload


def test_complete_playbook_run_accepts_dict_result_json(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))

    start = get_tool(server, "start_playbook_run").fn
    complete = get_tool(server, "complete_playbook_run").fn
    history = get_tool(server, "playbook_history").fn

    run_id = int(start("daily_review", "hermes", "cron", "0 21 * * *")["data"]["run_id"])
    original = {"review_path": "Minx/Reviews/2026-04-22.md", "count": 3}
    done = complete(run_id, "succeeded", True, True, original, None)
    assert done["success"] is True

    rows = history("daily_review", "hermes", "succeeded", None, 30, 20)
    assert rows["success"] is True
    assert rows["data"]["runs"][0]["result_json"] == original

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT result_json FROM playbook_runs WHERE id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["result_json"]) == original


def test_complete_playbook_run_still_accepts_string_result_json(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))

    start = get_tool(server, "start_playbook_run").fn
    complete = get_tool(server, "complete_playbook_run").fn
    history = get_tool(server, "playbook_history").fn

    run_id = int(start("daily_review", "hermes", "cron", "0 21 * * *")["data"]["run_id"])
    done = complete(run_id, "succeeded", True, True, '{"ok": true}', None)
    assert done["success"] is True
    rows = history("daily_review", "hermes", "succeeded", None, 30, 20)
    assert rows["data"]["runs"][0]["result_json"] == {"ok": True}


def test_complete_playbook_run_rejects_invalid_json_string(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))

    start = get_tool(server, "start_playbook_run").fn
    complete = get_tool(server, "complete_playbook_run").fn

    run_id = int(start("daily_review", "hermes", "cron", "0 21 * * *")["data"]["run_id"])
    result = complete(run_id, "succeeded", True, True, "not json at all", None)
    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_log_playbook_run_accepts_dict_result_json(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))

    log_run = get_tool(server, "log_playbook_run").fn
    history = get_tool(server, "playbook_history").fn

    original = {"generated": True, "files": ["a.md", "b.md"]}
    result = log_run(
        "weekly_report",
        "hermes",
        "cron",
        "0 10 * * 1",
        "succeeded",
        True,
        True,
        original,
        None,
    )
    assert result["success"] is True
    run_id = int(result["data"]["run_id"])

    rows = history("weekly_report", "hermes", "succeeded", None, 30, 20)
    assert rows["success"] is True
    assert rows["data"]["runs"][0]["result_json"] == original

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT result_json FROM playbook_runs WHERE id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    assert json.loads(row["result_json"]) == original


def test_log_playbook_run_still_accepts_string_result_json(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))

    log_run = get_tool(server, "log_playbook_run").fn
    history = get_tool(server, "playbook_history").fn

    result = log_run(
        "weekly_report",
        "hermes",
        "cron",
        "0 10 * * 1",
        "succeeded",
        True,
        True,
        '{"generated": true}',
        None,
    )
    assert result["success"] is True

    rows = history("weekly_report", "hermes", "succeeded", None, 30, 20)
    assert rows["data"]["runs"][0]["result_json"] == {"generated": True}


def test_log_playbook_run_rejects_invalid_json_string(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))

    log_run = get_tool(server, "log_playbook_run").fn

    result = log_run(
        "weekly_report",
        "hermes",
        "cron",
        "0 10 * * 1",
        "succeeded",
        True,
        True,
        "not json at all",
        None,
    )
    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"
