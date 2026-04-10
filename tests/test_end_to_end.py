from __future__ import annotations

from pathlib import Path

import pytest

from minx_mcp.core.events import emit_event
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from minx_mcp.finance.service import FinanceService


def test_import_to_summary_to_report_flow(tmp_path):
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    vault = tmp_path / "vault"
    service = FinanceService(tmp_path / "minx.db", vault)
    imported = service.finance_import(str(source), account_name="Robinhood Gold")
    summary = service.safe_finance_summary()
    report = service.generate_monthly_report("2026-03-01", "2026-03-31")
    assert imported["result"]["inserted"] == 1
    assert summary["categories"]
    assert isinstance(summary["net_total"], float)
    assert summary["net_total"] == -12.5
    assert report["summary"]["account_rollups"][0]["total_amount"] == -12.5
    assert report["vault_path"].endswith("Finance/monthly-2026-03.md")
    row = service.conn.execute(
        """
        SELECT status, error_message, vault_path
        FROM finance_report_runs
        WHERE report_kind = 'monthly' AND period_start = '2026-03-01' AND period_end = '2026-03-31'
        """
    ).fetchone()
    assert row["status"] == "completed"
    assert row["error_message"] is None
    assert row["vault_path"] == report["vault_path"]


@pytest.mark.asyncio
async def test_goal_parse_to_snapshot_flow_exercises_repo_scoped_core_contracts(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'seed-fingerprint')
        """
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id,
            batch_id,
            posted_at,
            description,
            merchant,
            amount_cents,
            category_id,
            category_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, 1, "2026-03-15", "Lunch", "Cafe", -1200, 3, "manual"),
    )
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T15:00:00Z",
        entity_ref="batch-1",
        source="tests",
        payload={
            "account_name": "DCU",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 1,
            "total_cents": -1200,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()

    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_parse = server._tool_manager.get_tool("goal_parse").fn
    goal_create = server._tool_manager.get_tool("goal_create").fn
    goal_get = server._tool_manager.get_tool("goal_get").fn
    goal_update = server._tool_manager.get_tool("goal_update").fn
    daily_snapshot = server._tool_manager.get_tool("get_daily_snapshot").fn

    capture_create = await goal_parse(
        message="Make a goal to spend less than $250 on dining out this month",
        review_date="2026-03-15",
    )
    assert capture_create["success"] is True
    assert capture_create["data"]["result_type"] == "create"

    created = goal_create(**capture_create["data"]["payload"])
    assert created["success"] is True
    goal_id = created["data"]["goal"]["id"]

    progress_before = goal_get(goal_id=goal_id, review_date="2026-03-15")
    assert progress_before["success"] is True
    assert progress_before["data"]["progress"]["actual_value"] == 1200

    capture_update = await goal_parse(
        message="Pause my dining out goal",
        review_date="2026-03-15",
    )
    assert capture_update["success"] is True
    assert capture_update["data"]["result_type"] == "update"

    updated = goal_update(
        goal_id=capture_update["data"]["goal_id"],
        **capture_update["data"]["payload"],
    )
    assert updated["success"] is True
    assert updated["data"]["goal"]["status"] == "paused"

    snapshot = await daily_snapshot("2026-03-15", False)
    assert snapshot["success"] is True
    assert snapshot["data"]["date"] == "2026-03-15"
    assert snapshot["data"]["goal_progress"] == []
    assert "signals" in snapshot["data"]


class _TestConfig:
    def __init__(self, db_path: Path, vault_path: Path) -> None:
        self._db_path = db_path
        self._vault_path = vault_path

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def vault_path(self) -> Path:
        return self._vault_path
