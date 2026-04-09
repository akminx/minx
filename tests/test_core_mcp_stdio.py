from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from minx_mcp.core.events import emit_event
from minx_mcp.db import get_connection


@pytest.mark.asyncio
async def test_core_server_stdio_goal_capture_flow(tmp_path: Path) -> None:
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

    env = os.environ.copy()
    env["MINX_DB_PATH"] = str(db_path)
    env["MINX_VAULT_PATH"] = str(tmp_path / "vault")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "minx_mcp.core", "--transport", "stdio"],
        env=env,
        cwd=Path.cwd(),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialize_result = await session.initialize()
            assert initialize_result.serverInfo.name == "minx-core"

            tools_result = await session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]
            assert tool_names == [
                "daily_review",
                "goal_create",
                "goal_list",
                "goal_get",
                "goal_update",
                "goal_archive",
                "goal_capture",
            ]

            captured_create = await session.call_tool(
                "goal_capture",
                {
                    "message": "Make a goal to spend less than $250 on dining out this month",
                    "review_date": "2026-03-15",
                },
            )
            assert captured_create.isError is False
            assert captured_create.structuredContent["success"] is True
            assert captured_create.structuredContent["data"]["result_type"] == "create"
            assert captured_create.structuredContent["data"]["payload"]["starts_on"] == "2026-03-01"

            created = await session.call_tool(
                "goal_create",
                captured_create.structuredContent["data"]["payload"],
            )
            assert created.isError is False
            assert created.structuredContent["success"] is True
            assert created.structuredContent["data"]["goal"]["domain"] == "finance"
            goal_id = created.structuredContent["data"]["goal"]["id"]

            fetched = await session.call_tool(
                "goal_get",
                {"goal_id": goal_id, "review_date": "2026-03-15"},
            )
            assert fetched.isError is False
            assert fetched.structuredContent["success"] is True
            assert fetched.structuredContent["data"]["goal"]["title"] == "Dining Out Spending Cap"
            assert fetched.structuredContent["data"]["progress"]["actual_value"] == 1200

            captured_update = await session.call_tool(
                "goal_capture",
                {"message": "Pause my dining out goal", "review_date": "2026-03-15"},
            )
            assert captured_update.isError is False
            assert captured_update.structuredContent["success"] is True
            assert captured_update.structuredContent["data"]["result_type"] == "update"

            updated = await session.call_tool(
                "goal_update",
                {
                    "goal_id": captured_update.structuredContent["data"]["goal_id"],
                    **captured_update.structuredContent["data"]["payload"],
                },
            )
            assert updated.isError is False
            assert updated.structuredContent["success"] is True
            assert updated.structuredContent["data"]["goal"]["status"] == "paused"

            review = await session.call_tool(
                "daily_review",
                {"review_date": "2026-03-15", "force": False},
            )
            assert review.isError is False
            assert review.structuredContent["success"] is True
            assert review.structuredContent["data"]["date"] == "2026-03-15"
            assert review.structuredContent["data"]["redaction_applied"] is True
            assert "goal_progress" not in review.structuredContent["data"]
            assert review.structuredContent["data"]["redaction_policy"] == "core_default_v1"
            assert "markdown" not in review.structuredContent["data"]
