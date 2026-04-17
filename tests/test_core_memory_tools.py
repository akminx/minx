from __future__ import annotations

from pathlib import Path

from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from tests.helpers import MinxTestConfig, get_tool


def test_memory_tools_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))

    for name in (
        "memory_list",
        "memory_get",
        "memory_create",
        "memory_confirm",
        "memory_reject",
        "memory_expire",
        "get_pending_memory_candidates",
    ):
        assert get_tool(server, name) is not None

    create_fn = get_tool(server, "memory_create").fn
    created = create_fn(
        "preference",
        "core",
        "timezone_pref",
        0.5,
        {"tz": "UTC"},
        "user:vault",
        "stated in chat",
    )
    assert created["success"] is True
    mid = int(created["data"]["memory"]["id"])

    listed = get_tool(server, "memory_list").fn(None, None, 10)
    assert listed["success"] is True
    assert len(listed["data"]["memories"]) >= 1

    got = get_tool(server, "memory_get").fn(mid)
    assert got["success"] is True
    assert got["data"]["memory"]["subject"] == "timezone_pref"

    conf = get_tool(server, "memory_confirm").fn(mid)
    assert conf["success"] is True
    assert conf["data"]["memory"]["status"] == "active"

    low = create_fn("preference", "core", "low_conf", 0.4, {}, "user", "")
    assert low["success"] is True
    low_id = int(low["data"]["memory"]["id"])
    pending = get_tool(server, "get_pending_memory_candidates").fn(10)
    assert pending["success"] is True
    subjects = {m["subject"] for m in pending["data"]["memories"]}
    assert "low_conf" in subjects

    rej = get_tool(server, "memory_reject").fn(low_id, "nope")
    assert rej["success"] is True

    exp = get_tool(server, "memory_expire").fn(mid, "done")
    assert exp["success"] is True
    assert exp["data"]["memory"]["status"] == "expired"
