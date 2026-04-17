from __future__ import annotations

import sqlite3
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
        "list_snapshot_archives",
        "get_snapshot_archive",
    ):
        assert get_tool(server, name) is not None

    create_fn = get_tool(server, "memory_create").fn
    created = create_fn(
        "preference",
        "core",
        "timezone_pref",
        0.5,
        {"category": "timezone", "value": "UTC"},
        "user:vault",
        "stated in chat",
    )
    assert created["success"] is True
    mid = int(created["data"]["memory"]["id"])

    listed = get_tool(server, "memory_list").fn(None, None, None, 10)
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
    pending = get_tool(server, "get_pending_memory_candidates").fn(None, 10)
    assert pending["success"] is True
    subjects = {m["subject"] for m in pending["data"]["memories"]}
    assert "low_conf" in subjects

    rej = get_tool(server, "memory_reject").fn(low_id, "nope")
    assert rej["success"] is True

    exp = get_tool(server, "memory_expire").fn(mid, "done")
    assert exp["success"] is True
    assert exp["data"]["memory"]["status"] == "expired"


def test_memory_expire_tool_uses_system_actor_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "actor.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_fn = get_tool(server, "memory_create").fn
    expire_fn = get_tool(server, "memory_expire").fn

    created = create_fn(
        "preference",
        "core",
        "actor_test_subject",
        0.9,
        {"category": "k", "value": "v"},
        "user",
        "",
    )
    assert created["success"] is True
    mid = int(created["data"]["memory"]["id"])

    expired = expire_fn(mid, "ttl cleanup")
    assert expired["success"] is True

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT actor FROM memory_events WHERE memory_id = ? AND event_type = 'expired' ORDER BY id DESC LIMIT 1",
            (mid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "system"


def test_memory_create_duplicate_live_triple_returns_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_fn = get_tool(server, "memory_create").fn

    first = create_fn(
        "preference",
        "core",
        "tz",
        0.9,
        {"category": "timezone", "value": "UTC"},
        "user",
        "",
    )
    assert first["success"] is True

    dup = create_fn(
        "preference",
        "core",
        "tz",
        0.4,
        {"category": "timezone", "value": "America/Los_Angeles"},
        "user",
        "",
    )
    assert dup["success"] is False
    assert dup["error_code"] == "CONFLICT"
    assert dup["data"] == {
        "memory_type": "preference",
        "scope": "core",
        "subject": "tz",
    }


def test_memory_list_and_pending_scope_filter(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_fn = get_tool(server, "memory_create").fn
    list_fn = get_tool(server, "memory_list").fn
    pending_fn = get_tool(server, "get_pending_memory_candidates").fn

    create_fn("preference", "finance", "fin_a", 0.4, {}, "user", "")
    create_fn("preference", "meals", "meal_a", 0.4, {}, "user", "")
    create_fn("preference", "finance", "fin_b", 0.9, {}, "user", "")

    listed_fin = list_fn(None, None, "finance", 10)
    assert listed_fin["success"] is True
    assert {m["subject"] for m in listed_fin["data"]["memories"]} == {"fin_a", "fin_b"}

    listed_meals = list_fn(None, None, "meals", 10)
    assert listed_meals["success"] is True
    assert {m["subject"] for m in listed_meals["data"]["memories"]} == {"meal_a"}

    pending_fin = pending_fn("finance", 10)
    assert pending_fin["success"] is True
    assert {m["subject"] for m in pending_fin["data"]["memories"]} == {"fin_a"}

    pending_all = pending_fn(None, 10)
    assert pending_all["success"] is True
    assert {m["subject"] for m in pending_all["data"]["memories"]} == {"fin_a", "meal_a"}

    pending_whitespace = pending_fn("   ", 10)
    assert pending_whitespace["success"] is True
    assert {m["subject"] for m in pending_whitespace["data"]["memories"]} == {"fin_a", "meal_a"}


def test_memory_create_mcp_returns_invalid_input_for_bad_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "bad_payload.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_fn = get_tool(server, "memory_create").fn
    result = create_fn(
        "preference",
        "core",
        "bad_payload_subject",
        0.9,
        {"not_a_valid_preference_key": True},
        "user",
        "",
    )
    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"
    assert result["error"] is not None
    assert "not_a_valid_preference_key" in result["error"]
