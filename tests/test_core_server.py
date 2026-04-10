from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from minx_mcp.core.events import emit_event
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection


def _call_tool_sync(fn, *args, **kwargs):
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def test_core_server_registers_slice25_tool_names(tmp_path: Path) -> None:
    server = create_core_server(_TestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    assert server._tool_manager.get_tool("get_daily_snapshot").name == "get_daily_snapshot"
    assert server._tool_manager.get_tool("get_insight_history").name == "get_insight_history"
    assert server._tool_manager.get_tool("get_goal_trajectory").name == "get_goal_trajectory"
    assert server._tool_manager.get_tool("persist_note").name == "persist_note"
    assert server._tool_manager.get_tool("goal_parse").name == "goal_parse"


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
    snapshot_tool = server._tool_manager.get_tool("get_daily_snapshot").fn

    result = await snapshot_tool("2026-03-15", False)

    assert result["success"] is True
    assert result["data"]["date"] == "2026-03-15"
    assert "signals" in result["data"]
    assert "attention_items" in result["data"]
    assert "narrative" not in result["data"]


def test_persist_note_creates_and_conflicts_without_overwrite(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    persist_note = server._tool_manager.get_tool("persist_note").fn

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
    history_tool = server._tool_manager.get_tool("get_insight_history").fn

    result = history_tool(28, None, None, "2026-03-31")

    assert result["success"] is True
    assert result["data"]["insights"][0]["summary"] == "Spike"


def test_get_goal_trajectory_tool_returns_invalid_input_for_bad_date(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    trajectory_tool = server._tool_manager.get_tool("get_goal_trajectory").fn

    result = trajectory_tool(1, 4, "not-a-date")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


class _TestConfig:
    def __init__(self, db_path: Path, vault_path: Path) -> None:
        self.db_path = db_path
        self.vault_path = vault_path


def _seed_transaction(conn, *, posted_at: str, amount_cents: int) -> None:
    category_id = conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Dining Out'"
    ).fetchone()["id"]
    account_id = conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'DCU'"
    ).fetchone()["id"]
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
