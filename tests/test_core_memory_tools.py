from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
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
        "vault_scan",
        "vault_reconcile_memories",
        "list_snapshot_archives",
        "get_snapshot_archive",
        "start_playbook_run",
        "complete_playbook_run",
        "log_playbook_run",
        "playbook_history",
        "playbook_reconcile_crashed",
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


def test_vault_scan_tool_syncs_memory_note(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    vault = tmp_path / "vault"
    get_connection(db_path).close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        (
            "---\n"
            "type: minx-memory\n"
            "domain: core\n"
            "memory_key: core.preference.timezone\n"
            "memory_type: preference\n"
            "subject: timezone\n"
            "value: UTC\n"
            "---\n"
        ),
        encoding="utf-8",
    )
    server = create_core_server(MinxTestConfig(db_path, vault))

    scanned = get_tool(server, "vault_scan").fn(False)

    assert scanned["success"] is True
    assert scanned["data"]["report"]["scanned"] == 1
    assert scanned["data"]["report"]["memory_syncs"] == 1
    listed = get_tool(server, "memory_list").fn("active", "preference", "core", 10)
    assert listed["data"]["memories"][0]["subject"] == "timezone"


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
    first_id = int(first["data"]["memory"]["id"])

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
        "conflict_kind": "structural_triple",
        "memory_id": first_id,
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


# Issue 2 regression: vault_scan must write an audit_log row.
def test_vault_scan_writes_audit_log_row(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    vault = tmp_path / "vault"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, vault))

    vault_scan_fn = get_tool(server, "vault_scan").fn
    result = vault_scan_fn(False)
    assert result["success"] is True

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT tool_name, summary FROM audit_log WHERE tool_name = 'vault_scan' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "vault_scan must write an audit_log entry"
    assert row[0] == "vault_scan"
    assert "dry_run=False" in row[1]


def test_vault_scan_dry_run_writes_audit_log_row(tmp_path: Path) -> None:
    db_path = tmp_path / "audit_dry.db"
    vault = tmp_path / "vault"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, vault))

    vault_scan_fn = get_tool(server, "vault_scan").fn
    result = vault_scan_fn(True)
    assert result["success"] is True

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT tool_name, summary FROM audit_log WHERE tool_name = 'vault_scan' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert "dry_run=True" in row[1]


# Issue 6 regression: prune_expired_memories must not commit inside an outer transaction.
def test_prune_expired_memories_does_not_commit_outer_transaction(tmp_path: Path) -> None:
    from minx_mcp.core.memory_service import MemoryService

    db_path = tmp_path / "prune.db"
    conn = get_connection(db_path)
    service = MemoryService(db_path, conn=conn)

    memory = service.create_memory(
        memory_type="preference",
        scope="core",
        subject="prune_test",
        confidence=0.5,
        payload={},
        source="detector",
        actor="detector",
    )
    conn.commit()

    # Reject the memory so it gets a TTL.
    service.reject_memory(memory.id, actor="user", reason="test")
    conn.commit()

    # Set expires_at to past.
    conn.execute(
        "UPDATE memories SET expires_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(hours=1)).isoformat(), memory.id),
    )
    conn.commit()

    # Begin an explicit outer transaction, call prune, then rollback.
    conn.execute("BEGIN IMMEDIATE")
    count = service.prune_expired_memories()
    assert count == 1

    # Row should be gone from the service's view but outer transaction is not yet committed.
    # Rollback must undo the delete.
    conn.rollback()

    row = conn.execute("SELECT id FROM memories WHERE id = ?", (memory.id,)).fetchone()
    assert row is not None, "prune_expired_memories must not auto-commit; rollback must undo the delete"


# Issue 10 regression: memory_list with status=None must also prune expired rejected rows.
def test_memory_list_with_no_status_prunes_expired_rejected_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "list_prune.db"
    vault = tmp_path / "vault"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, vault))
    create_fn = get_tool(server, "memory_create").fn
    list_fn = get_tool(server, "memory_list").fn

    # Create and reject a memory.
    mem = create_fn("preference", "core", "to_be_pruned", 0.9, {}, "user", "")
    mid = int(mem["data"]["memory"]["id"])
    get_tool(server, "memory_reject").fn(mid, "no longer needed")

    # Directly set expires_at to the past so prune picks it up.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE memories SET expires_at = ? WHERE id = ?",
            ((datetime.now(UTC) - timedelta(hours=1)).isoformat(), mid),
        )
        conn.commit()
    finally:
        conn.close()

    # Call memory_list with status=None (previously did NOT call prune_expired_memories).
    listed = list_fn(None, None, None, 100)
    assert listed["success"] is True
    ids = [m["id"] for m in listed["data"]["memories"]]
    assert mid not in ids, "Expired rejected memory must be pruned when memory_list(status=None) is called"


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
