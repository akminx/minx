from __future__ import annotations

import pytest

from minx_mcp.core.events import emit_event
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from tests.helpers import MinxTestConfig, get_tool
from tests.test_snapshot import _seed_goal, _seed_transaction


def _seed_review_day(db_path) -> None:
    conn = get_connection(db_path)
    _seed_goal(conn)
    _seed_transaction(
        conn,
        posted_at="2026-03-15",
        merchant="Cafe",
        amount_cents=-6800,
        category_name="Dining Out",
    )
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T12:00:00Z",
        entity_ref="job:1",
        source="tests",
        payload={
            "account_name": "DCU",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 1,
            "total_cents": -6800,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_list_and_get_snapshot_archive_tools(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    _seed_review_day(db_path)

    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    snapshot_tool = get_tool(server, "get_daily_snapshot").fn
    snap_result = await snapshot_tool("2026-03-15", False)
    assert snap_result["success"] is True

    list_fn = get_tool(server, "list_snapshot_archives").fn
    listed_all = list_fn(None, 30)
    assert listed_all["success"] is True
    assert len(listed_all["data"]["archives"]) == 1
    row = listed_all["data"]["archives"][0]
    assert set(row) == {"id", "review_date", "generated_at", "content_hash", "source"}
    assert row["review_date"] == "2026-03-15"
    archive_id = int(row["id"])

    listed_day = list_fn("2026-03-15", 10)
    assert listed_day["success"] is True
    assert len(listed_day["data"]["archives"]) == 1
    assert int(listed_day["data"]["archives"][0]["id"]) == archive_id

    get_fn = get_tool(server, "get_snapshot_archive").fn
    got = get_fn(archive_id)
    assert got["success"] is True
    inner = got["data"]["archive"]
    assert inner["id"] == archive_id
    assert inner["review_date"] == "2026-03-15"
    assert inner["content_hash"] == row["content_hash"]
    assert isinstance(inner["snapshot"], dict)
    assert inner["snapshot"]["date"] == "2026-03-15"


def test_get_snapshot_archive_invalid_id(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    get_fn = get_tool(server, "get_snapshot_archive").fn
    missing = get_fn(999)
    assert missing["success"] is False
    assert missing["error_code"] == "NOT_FOUND"


def test_list_snapshot_archives_rejects_bad_date(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    list_fn = get_tool(server, "list_snapshot_archives").fn
    bad = list_fn("not-a-date", 5)
    assert bad["success"] is False
    assert bad["error_code"] == "INVALID_INPUT"
