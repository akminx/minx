from __future__ import annotations

from pathlib import Path

import pytest

from minx_mcp.core.events import emit_event
from minx_mcp.core.models import GoalCaptureResult
from minx_mcp.core.server import create_core_server
from minx_mcp.core.tools import goals as goal_tools_module
from minx_mcp.db import get_connection
from tests.helpers import MinxTestConfig, get_tool


def test_core_server_registers_slice25_tool_names(tmp_path: Path) -> None:
    server = create_core_server(_TestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    expected_tools = {
        "get_daily_snapshot",
        "get_insight_history",
        "get_goal_trajectory",
        "persist_note",
        "goal_parse",
        "memory_list",
        "memory_get",
        "memory_create",
        "memory_confirm",
        "memory_reject",
        "memory_expire",
        "get_pending_memory_candidates",
        "start_investigation",
        "append_investigation_step",
        "complete_investigation",
        "log_investigation",
        "investigation_history",
        "investigation_get",
        "list_snapshot_archives",
        "get_snapshot_archive",
    }
    import asyncio

    registered = {t.name for t in asyncio.run(server.list_tools())}
    assert expected_tools.issubset(registered)


@pytest.mark.asyncio
async def test_get_daily_snapshot_tool_returns_structured_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_transaction(conn, posted_at="2026-03-15", amount_cents=-6800)
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T15:00:00Z",
        entity_ref="job:1",
        source="tests",
        payload={
            "account_name": "DCU",
            "job_id": "job-1",
            "transaction_count": 1,
            "total_cents": -6800,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()

    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    snapshot_tool = get_tool(server, "get_daily_snapshot").fn

    result = await snapshot_tool("2026-03-15", False)

    assert result["success"] is True
    assert result["data"]["date"] == "2026-03-15"
    signals = result["data"]["signals"]
    assert isinstance(signals, list)
    for sig in signals:
        assert "insight_type" in sig
        assert "summary" in sig
        assert "severity" in sig
    assert "attention_items" in result["data"]
    assert "narrative" not in result["data"]


def test_persist_note_creates_and_conflicts_without_overwrite(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    persist_note = get_tool(server, "persist_note").fn

    created = persist_note("Minx/Reviews/test.md", "# hi", False)
    conflicted = persist_note("Minx/Reviews/test.md", "# hi", False)

    assert created["success"] is True
    assert created["data"]["created"] is True
    assert conflicted["success"] is False
    assert conflicted["error_code"] == "CONFLICT"


def test_get_insight_history_tool_wraps_history_result(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO insights (
            insight_type, dedupe_key, summary, supporting_signals, confidence,
            severity, actionability, source, review_date, expires_at, created_at
        ) VALUES (
            'finance.spending_spike', '2026-03-10:spending_spike:dining-out', 'Spike',
            '[]', 0.9, 'warning', 'suggestion', 'detector', '2026-03-10', NULL, datetime('now')
        )
        """
    )
    conn.commit()
    conn.close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    history_tool = get_tool(server, "get_insight_history").fn

    result = history_tool(28, None, None, "2026-03-31")

    assert result["success"] is True
    assert result["data"]["insights"][0]["summary"] == "Spike"


@pytest.mark.asyncio
async def test_goal_parse_does_not_hold_db_connection_across_llm_await(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    config = _TestConfig(db_path, tmp_path / "vault")
    called = {"n": 0}

    async def fake_parse_goal_input(*args: object, **kwargs: object) -> GoalCaptureResult:
        called["n"] += 1
        second = get_connection(config.db_path)
        try:
            second.execute("BEGIN IMMEDIATE")
            second.execute("ROLLBACK")
        finally:
            second.close()
        return GoalCaptureResult(result_type="no_match", assistant_message="probe ok")

    monkeypatch.setattr(goal_tools_module, "parse_goal_input", fake_parse_goal_input)
    server = create_core_server(config)
    goal_parse = get_tool(server, "goal_parse").fn

    result = await goal_parse(message="save fifty dollars monthly on groceries", review_date=None)

    assert result["success"] is True
    assert result["data"]["result_type"] == "no_match"
    assert result["data"]["assistant_message"] == "probe ok"
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_goal_parse_persists_result_after_llm_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """goal_parse does not mutate goals; ensure a seeded goal survives the handler."""
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO goals (
            goal_type, title, status, metric_type, target_value, period, domain,
            filters_json, starts_on, ends_on, notes, created_at, updated_at
        ) VALUES (
            'spending_cap', 'Coffee Cap', 'active', 'sum_below', 4000, 'monthly', 'finance',
            '{"category_names":[],"merchant_names":["Starbucks"],"account_names":[]}',
            '2026-03-01', NULL, NULL, datetime('now'), datetime('now')
        )
        """
    )
    conn.commit()
    conn.close()
    config = _TestConfig(db_path, tmp_path / "vault")

    async def fake_parse_goal_input(*args: object, **kwargs: object) -> GoalCaptureResult:
        return GoalCaptureResult(
            result_type="update",
            action="goal_update",
            goal_id=1,
            payload={"status": "paused"},
            assistant_message="Paused.",
        )

    monkeypatch.setattr(goal_tools_module, "parse_goal_input", fake_parse_goal_input)
    server = create_core_server(config)
    goal_parse = get_tool(server, "goal_parse").fn

    result = await goal_parse(message="pause my coffee goal", review_date="2026-03-15")

    assert result["success"] is True
    assert result["data"]["result_type"] == "update"
    assert result["data"]["goal_id"] == 1
    verify = get_connection(db_path)
    try:
        row = verify.execute("SELECT id, title FROM goals WHERE id = 1").fetchone()
    finally:
        verify.close()
    assert row is not None
    assert row["title"] == "Coffee Cap"


def test_get_goal_trajectory_tool_returns_invalid_input_for_bad_date(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    trajectory_tool = get_tool(server, "get_goal_trajectory").fn

    result = trajectory_tool(1, 4, "not-a-date")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


_TestConfig = MinxTestConfig


def _seed_transaction(conn, *, posted_at: str, amount_cents: int) -> None:
    category_id = conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Dining Out'"
    ).fetchone()["id"]
    account_id = conn.execute("SELECT id FROM finance_accounts WHERE name = 'DCU'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, ?, 'csv', 'seed.csv', 'fp')
        """,
        (account_id,),
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, 1, ?, 'Meal', 'Cafe', ?, ?, 'manual')
        """,
        (account_id, posted_at, amount_cents, category_id),
    )
