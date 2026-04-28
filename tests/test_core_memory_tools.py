from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from tests.helpers import MinxTestConfig, get_tool


def _fake_github_token() -> str:
    return "".join(("gh", "p_", "a" * 36))


def _fake_private_key_block() -> str:
    return "\n".join(
        (
            "-----" + "BEGIN PRIVATE KEY" + "-----",
            "a" * 64,
            "-----" + "END PRIVATE KEY" + "-----",
        )
    )


def test_memory_tools_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))

    for name in (
        "memory_list",
        "memory_get",
        "memory_create",
        "memory_capture",
        "memory_confirm",
        "memory_reject",
        "memory_expire",
        "memory_search",
        "memory_hybrid_search",
        "memory_embedding_enqueue",
        "memory_embedding_status",
        "memory_edge_create",
        "memory_edge_list",
        "memory_edge_delete",
        "enrichment_sweep",
        "enrichment_status",
        "enrichment_retry_dead_letter",
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


def test_memory_create_secret_block_surfaces_invalid_input_without_secret(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_fn = get_tool(server, "memory_create").fn
    secret = _fake_private_key_block()

    blocked = create_fn("preference", "core", "blocked", 0.9, {"value": secret}, "user", "")

    assert blocked["success"] is False
    assert blocked["error_code"] == "INVALID_INPUT"
    assert blocked["data"]["kind"] == "secret_detected"
    assert blocked["data"]["surface"] == "memory"
    assert secret not in str(blocked)


def test_memory_capture_happy_path_candidate(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    capture_fn = get_tool(server, "memory_capture").fn

    out = capture_fn(
        text="Pick up laundry after 5pm",
        capture_type="observation",
        scope="core",
        subject=None,
        source="user:capture",
        confidence=0.5,
        metadata=None,
    )

    assert out["success"] is True
    memory = out["data"]["memory"]
    assert memory["memory_type"] == "captured_thought"
    assert memory["status"] == "candidate"
    assert memory["confidence"] == 0.5
    assert out["data"]["response_template"] == "memory_capture.created_candidate"
    assert out["data"]["response_slots"] == {
        "memory_id": memory["id"],
        "status": "candidate",
        "memory_type": "captured_thought",
        "scope": "core",
        "subject": memory["subject"],
        "capture_type": "observation",
    }
    assert memory["payload"] == {
        "text": "Pick up laundry after 5pm",
        "capture_type": "observation",
    }


def test_memory_capture_with_metadata_and_explicit_subject(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    capture_fn = get_tool(server, "memory_capture").fn

    out = capture_fn(
        text="body text",
        capture_type="todo",
        scope="core",
        subject="my_subject",
        source="user:capture",
        confidence=0.4,
        metadata={"src": "chat"},
    )

    assert out["success"] is True
    memory = out["data"]["memory"]
    assert memory["subject"] == "my_subject"
    assert memory["payload"] == {
        "text": "body text",
        "capture_type": "todo",
        "metadata": {"src": "chat"},
    }


def test_memory_capture_rejects_high_confidence(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    capture_fn = get_tool(server, "memory_capture").fn

    out = capture_fn("x", "observation", "core", None, "user:capture", 0.8, None)

    assert out["success"] is False
    assert out["error_code"] == "INVALID_INPUT"


def test_memory_capture_rejects_invalid_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    capture_fn = get_tool(server, "memory_capture").fn

    out = capture_fn("x", "observation", "core", None, "user:capture", 0.5, {"bad": {1, 2}})

    assert out["success"] is False
    assert out["error_code"] == "INVALID_INPUT"


def test_memory_capture_secret_blocked_like_create(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    capture_fn = get_tool(server, "memory_capture").fn
    secret = _fake_private_key_block()

    out = capture_fn(secret, "observation", "core", None, "user:capture", 0.5, None)

    assert out["success"] is False
    assert out["error_code"] == "INVALID_INPUT"
    assert secret not in str(out)


def test_memory_capture_derived_subject_stable(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    capture_fn = get_tool(server, "memory_capture").fn

    first = capture_fn("  hello  \nworld", "Note", "core", None, "user:capture", 0.5, None)
    second = capture_fn("  hello  \nworld", "Note", "core", None, "user:capture", 0.5, None)

    assert first["success"] is True
    assert second["success"] is False
    assert second["error_code"] == "CONFLICT"
    assert first["data"]["memory"]["subject"] == "note:hello"


def test_memory_create_redacted_payload_returns_redacted_memory(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_fn = get_tool(server, "memory_create").fn
    secret = _fake_github_token()

    created = create_fn("preference", "core", "redacted", 0.9, {"value": secret}, "user", "")

    assert created["success"] is True
    assert created["data"]["memory"]["payload"] == {"value": "[REDACTED:github_token]"}
    assert secret not in str(created)


def test_persist_note_blocks_secret_bearing_frontmatter(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    vault = tmp_path / "vault"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, vault))
    persist_fn = get_tool(server, "persist_note").fn
    secret = _fake_github_token()
    content = "---\n" f"token: {secret}\n" "---\n# Title\n"

    blocked = persist_fn("Minx/secret.md", content, False)

    assert blocked["success"] is False
    assert blocked["error_code"] == "INVALID_INPUT"
    assert blocked["data"]["kind"] == "secret_detected"
    assert blocked["data"]["surface"] == "vault_frontmatter"
    assert secret not in str(blocked)
    assert not (vault / "Minx" / "secret.md").exists()


def test_persist_note_blocks_bom_prefixed_secret_bearing_frontmatter(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    vault = tmp_path / "vault"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, vault))
    persist_fn = get_tool(server, "persist_note").fn
    secret = _fake_github_token()
    content = "\ufeff---\n" f"token: {secret}\n" "---\n# Title\n"

    blocked = persist_fn("Minx/bom-secret.md", content, False)

    assert blocked["success"] is False
    assert blocked["error_code"] == "INVALID_INPUT"
    assert blocked["data"]["kind"] == "secret_detected"
    assert secret not in str(blocked)
    assert not (vault / "Minx" / "bom-secret.md").exists()


@pytest.mark.parametrize("prefix", ["---\n", "\ufeff---\n"])
def test_persist_note_blocks_unclosed_secret_bearing_frontmatter(tmp_path: Path, prefix: str) -> None:
    db_path = tmp_path / "m.db"
    vault = tmp_path / "vault"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, vault))
    persist_fn = get_tool(server, "persist_note").fn
    secret = _fake_github_token()
    content = prefix + f"token: {secret}\n# Title\n"

    blocked = persist_fn("Minx/unclosed-secret.md", content, False)

    assert blocked["success"] is False
    assert blocked["error_code"] == "INVALID_INPUT"
    assert blocked["data"]["kind"] == "secret_detected"
    assert secret not in str(blocked)
    assert not (vault / "Minx" / "unclosed-secret.md").exists()


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


def test_memory_search_tool_returns_ranked_results(tmp_path: Path) -> None:
    db_path = tmp_path / "search.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_fn = get_tool(server, "memory_create").fn
    search_fn = get_tool(server, "memory_search").fn

    created = create_fn(
        "preference",
        "core",
        "coffee",
        0.95,
        {"value": "prefers espresso after training"},
        "user",
        "manual",
    )
    assert created["success"] is True

    searched = search_fn("espresso", None, None, "active", 10)

    assert searched["success"] is True
    assert searched["data"]["results"][0]["memory"]["subject"] == "coffee"
    assert "espresso" in searched["data"]["results"][0]["snippet"].lower()
    assert isinstance(searched["data"]["results"][0]["rank"], float)


def test_memory_search_tool_invalid_inputs_return_invalid_input(tmp_path: Path) -> None:
    db_path = tmp_path / "invalid-search.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    search_fn = get_tool(server, "memory_search").fn

    blank = search_fn("   ", None, None, "active", 10)
    bad_limit = search_fn("coffee", None, None, "active", 0)

    assert blank["success"] is False
    assert blank["error_code"] == "INVALID_INPUT"
    assert bad_limit["success"] is False
    assert bad_limit["error_code"] == "INVALID_INPUT"


def test_memory_edge_tools_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "edge-tools.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_memory = get_tool(server, "memory_create").fn
    create_edge = get_tool(server, "memory_edge_create").fn
    list_edges = get_tool(server, "memory_edge_list").fn
    delete_edge = get_tool(server, "memory_edge_delete").fn

    source = create_memory("preference", "core", "new", 0.95, {"value": "new"}, "user", "")
    target = create_memory("preference", "core", "old", 0.95, {"value": "old"}, "user", "")
    assert source["success"] is True
    assert target["success"] is True

    created = create_edge(
        source["data"]["memory"]["id"],
        target["data"]["memory"]["id"],
        "supersedes",
        "newer version",
    )

    assert created["success"] is True
    edge_id = created["data"]["edge"]["id"]
    listed = list_edges(source["data"]["memory"]["id"], "outgoing", None, 10)
    assert listed["success"] is True
    assert [edge["id"] for edge in listed["data"]["edges"]] == [edge_id]
    deleted = delete_edge(edge_id)
    assert deleted["success"] is True
    assert deleted["data"] == {"deleted": True}


def test_memory_edge_create_invalid_predicate_returns_invalid_input(tmp_path: Path) -> None:
    db_path = tmp_path / "edge-invalid.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_memory = get_tool(server, "memory_create").fn
    create_edge = get_tool(server, "memory_edge_create").fn

    source = create_memory("preference", "core", "source", 0.95, {"value": "source"}, "user", "")
    target = create_memory("preference", "core", "target", 0.95, {"value": "target"}, "user", "")

    result = create_edge(
        source["data"]["memory"]["id"],
        target["data"]["memory"]["id"],
        "explains",
        "",
    )

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


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
