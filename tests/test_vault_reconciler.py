from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from minx_mcp.core.memory_service import MemoryService
from minx_mcp.core.server import create_core_server
from minx_mcp.core.vault_reconciler import (
    VaultReconciler,
    _MemoryIdentity,
    _row_updated_after_note_mtime,
    _SkipNote,
)
from minx_mcp.db import get_connection
from minx_mcp.vault_reader import VaultDocument
from minx_mcp.vault_writer import VaultWriter
from tests.helpers import MinxTestConfig, get_tool


def _server(tmp_path: Path):
    db_path = tmp_path / "minx.db"
    vault = tmp_path / "vault"
    get_connection(db_path).close()
    return db_path, vault, create_core_server(MinxTestConfig(db_path, vault))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _memory_row(db_path: Path, subject: str) -> sqlite3.Row:
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM memories WHERE subject = ?", (subject,)).fetchone()
        assert row is not None
        return row
    finally:
        conn.close()


def _event_types(db_path: Path, memory_id: int) -> list[str]:
    conn = get_connection(db_path)
    try:
        return [
            str(row["event_type"])
            for row in conn.execute(
                "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id",
                (memory_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def _count_rows(db_path: Path, table: str) -> int:
    conn = get_connection(db_path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def test_vault_reconcile_creates_memory_and_refreshes_frontmatter(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"category\":\"timezone\",\"value\":\"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n\n"
        "## Human Editable\n\n"
        "Keep this exact prose.\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["scanned"] == 1
    assert report["applied"] == 1
    assert report["created"] == 1
    assert report["warnings"] == []
    row = _memory_row(db_path, "timezone")
    assert row["status"] == "active"
    assert row["source"] == "vault_sync"
    assert json.loads(row["payload_json"]) == {
        "category": "timezone",
        "value": "America/Chicago",
    }
    text = note.read_text(encoding="utf-8")
    assert f"memory_id: {row['id']}" in text
    assert f"sync_base_updated_at: \"{row['updated_at']}\"" in text
    assert "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'" in text
    assert "Keep this exact prose." in text
    assert _event_types(db_path, int(row["id"])) == ["created", "promoted", "vault_synced"]


def test_vault_reconcile_detects_active_memory_version_conflict(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="user",
        actor="user",
    )
    conn.close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    original_note = (
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        f"memory_id: {memory.id}\n"
        "sync_base_updated_at: \"1999-01-01 00:00:00\"\n"
        "payload_json: '{\"category\":\"timezone\",\"value\":\"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n"
    )
    _write(note, original_note)

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["scanned"] == 1
    assert report["applied"] == 0
    assert report["skipped"] == 1
    assert report["conflicts"] == 1
    assert report["warnings"][0]["kind"] == "conflict"
    assert _memory_row(db_path, "timezone")["payload_json"] == '{"category": "timezone", "value": "UTC"}'
    assert note.read_text(encoding="utf-8") == original_note


def test_vault_reconcile_confirms_candidate_without_sync_base(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.5,
        payload={"category": "timezone", "value": "UTC"},
        source="detector",
        actor="detector",
    )
    conn.close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        f"memory_id: {memory.id}\n"
        "payload_json: '{\"category\":\"timezone\",\"value\":\"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["applied"] == 1
    assert report["confirmed"] == 1
    row = _memory_row(db_path, "timezone")
    assert row["status"] == "active"
    assert json.loads(row["payload_json"])["value"] == "America/Chicago"
    assert _event_types(db_path, int(row["id"])) == [
        "created",
        "confirmed",
        "payload_updated",
        "vault_synced",
    ]


def test_vault_reconcile_idempotent_reapply_skips_without_events(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="vault_sync",
        actor="vault_sync",
    )
    memory = svc.get_memory(memory.id)
    conn.close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        f"memory_id: {memory.id}\n"
        f"sync_base_updated_at: \"{memory.updated_at}\"\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"UTC\"}'\n"
        "---\n"
        "# Timezone\n",
    )
    before_bytes = note.read_bytes()
    time.sleep(0.01)
    before_mtime = note.stat().st_mtime_ns

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    assert result["data"]["report"]["applied"] == 0
    assert result["data"]["report"]["skipped"] == 1
    assert _event_types(db_path, memory.id) == ["created", "promoted"]
    assert note.read_bytes() == before_bytes
    assert note.stat().st_mtime_ns == before_mtime


def test_vault_reconcile_reports_identity_mismatch(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="vault_sync",
        actor="vault_sync",
    )
    conn.close()
    note = vault / "Minx" / "Memory" / "mismatch.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.language\n"
        "memory_type: preference\n"
        "subject: language\n"
        f"memory_id: {memory.id}\n"
        f"sync_base_updated_at: \"{memory.updated_at}\"\n"
        "payload_json: '{\"category\": \"preference\", \"value\": \"English\"}'\n"
        "---\n"
        "# Language\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["skipped"] == 1
    assert report["warnings"][0]["kind"] == "identity_mismatch"


def test_vault_reconcile_reports_missing_memory_id(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    note = vault / "Minx" / "Memory" / "missing.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "memory_id: 999\n"
        "sync_base_updated_at: \"2026-04-19 12:00:00\"\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"UTC\"}'\n"
        "---\n"
        "# Missing\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["skipped"] == 1
    assert report["warnings"][0]["kind"] == "missing_memory"
    assert _count_rows(db_path, "memories") == 0


def test_vault_reconcile_skips_latest_terminal_row_without_memory_id(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.7,
        payload={"category": "timezone", "value": "UTC"},
        source="detector",
        actor="detector",
    )
    svc.reject_memory(memory.id, reason="no")
    conn.close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["skipped"] == 1
    assert report["warnings"][0]["kind"] == "terminal_state"
    assert _memory_row(db_path, "timezone")["status"] == "rejected"


def test_vault_reconcile_write_failure_rolls_back_memory_create(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path, vault, server = _server(tmp_path)
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    def fail_replace_frontmatter(self, relative_path, frontmatter):
        raise OSError("disk full")

    monkeypatch.setattr(VaultWriter, "replace_frontmatter", fail_replace_frontmatter)

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["skipped"] == 1
    assert report["warnings"][0]["kind"] == "write_failed"
    assert _count_rows(db_path, "memories") == 0
    assert _count_rows(db_path, "memory_events") == 0


def test_vault_reconcile_accepts_legacy_value_json_and_refreshes_payload_json(
    tmp_path: Path,
) -> None:
    db_path, vault, server = _server(tmp_path)
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "value_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    row = _memory_row(db_path, "timezone")
    assert json.loads(row["payload_json"]) == {
        "category": "timezone",
        "value": "America/Chicago",
    }
    text = note.read_text(encoding="utf-8")
    assert "value_json:" not in text
    assert "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'" in text


def test_vault_reconcile_accepts_legacy_domain_alias_and_refreshes_scope(
    tmp_path: Path,
) -> None:
    db_path, vault, server = _server(tmp_path)
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "domain: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    row = _memory_row(db_path, "timezone")
    assert row["scope"] == "core"
    text = note.read_text(encoding="utf-8")
    assert "domain:" not in text
    assert "scope: core" in text

    before_bytes = note.read_bytes()
    time.sleep(0.01)
    before_mtime = note.stat().st_mtime_ns
    events_before = _event_types(db_path, int(row["id"]))

    second = get_tool(server, "vault_reconcile_memories").fn(False)

    assert second["success"] is True
    assert second["data"]["report"]["applied"] == 0
    assert second["data"]["report"]["skipped"] == 1
    assert note.read_bytes() == before_bytes
    assert note.stat().st_mtime_ns == before_mtime
    assert _event_types(db_path, int(row["id"])) == events_before


def test_vault_reconcile_memory_key_fallback_updates_live_vault_memory(
    tmp_path: Path,
) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="vault_sync",
        actor="vault_sync",
    )
    conn.close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["updated"] == 1
    row = _memory_row(db_path, "timezone")
    assert row["id"] == memory.id
    assert json.loads(row["payload_json"])["value"] == "America/Chicago"
    assert f"memory_id: {memory.id}" in note.read_text(encoding="utf-8")


def test_vault_reconcile_updates_non_vault_memory_when_sync_base_matches(
    tmp_path: Path,
) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="user",
        actor="user",
    )
    conn.close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        f"sync_base_updated_at: \"{memory.updated_at}\"\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    assert result["data"]["report"]["updated"] == 1
    assert json.loads(_memory_row(db_path, "timezone")["payload_json"])["value"] == (
        "America/Chicago"
    )


def test_vault_reconcile_conflicts_on_memory_id_without_sync_base_for_active(
    tmp_path: Path,
) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="vault_sync",
        actor="vault_sync",
    )
    conn.close()
    original_note = (
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        f"memory_id: {memory.id}\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n"
    )
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(note, original_note)

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["conflicts"] == 1
    assert report["warnings"][0]["kind"] == "conflict"
    assert note.read_text(encoding="utf-8") == original_note


def test_vault_reconcile_conflicts_for_non_vault_memory_without_sync_base(
    tmp_path: Path,
) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="user",
        actor="user",
    )
    conn.close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["conflicts"] == 1
    assert report["warnings"][0]["kind"] == "conflict"
    assert json.loads(_memory_row(db_path, "timezone")["payload_json"])["value"] == "UTC"


def test_vault_reconcile_invalid_payload_validation_is_invalid_note(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"unknown\": \"field\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["skipped"] == 1
    assert report["warnings"][0]["kind"] == "invalid_note"
    assert _count_rows(db_path, "memories") == 0


def test_vault_reconcile_hand_authored_implicit_payload_is_canonicalized(
    tmp_path: Path,
) -> None:
    db_path, vault, server = _server(tmp_path)
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "category: timezone\n"
        "value: America/Chicago\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    assert result["data"]["report"]["created"] == 1
    row = _memory_row(db_path, "timezone")
    assert json.loads(row["payload_json"]) == {
        "category": "timezone",
        "value": "America/Chicago",
    }
    text = note.read_text(encoding="utf-8")
    assert "category: timezone" not in text
    assert "value: America/Chicago" not in text
    assert "payload_json:" in text


def test_vault_reconcile_generated_note_rejects_implicit_payload(
    tmp_path: Path,
) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="vault_sync",
        actor="vault_sync",
    )
    conn.close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        f"memory_id: {memory.id}\n"
        f"sync_base_updated_at: \"{memory.updated_at}\"\n"
        "category: timezone\n"
        "value: America/Chicago\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["skipped"] == 1
    assert report["warnings"][0]["kind"] == "invalid_note"
    assert json.loads(_memory_row(db_path, "timezone")["payload_json"])["value"] == "UTC"


def test_vault_reconcile_insert_without_rowid_becomes_write_failed_warning() -> None:
    class NoRowIdCursor:
        lastrowid = None

    class NoRowIdConnection:
        def execute(self, *args, **kwargs):
            return NoRowIdCursor()

    reconciler = VaultReconciler(NoRowIdConnection(), None, None)
    doc = VaultDocument(
        relative_path="Minx/Memory/timezone.md",
        frontmatter={},
        body="",
        content_hash="hash",
    )
    identity = _MemoryIdentity(
        scope="core",
        memory_type="preference",
        subject="timezone",
        memory_key="core.preference.timezone",
        memory_id=None,
        sync_base_updated_at=None,
    )

    try:
        reconciler._create_memory(
            doc,
            identity,
            {"category": "timezone", "value": "UTC"},
        )
    except _SkipNote as exc:
        assert exc.warning.kind == "write_failed"
        assert exc.warning.memory_key == "core.preference.timezone"
    else:
        raise AssertionError("expected write_failed skip")


def test_row_updated_after_note_mtime_logs_malformed_updated_at(
    tmp_path: Path,
    caplog,
) -> None:
    note = tmp_path / "note.md"
    note.write_text("# Note\n", encoding="utf-8")

    result = _row_updated_after_note_mtime({"id": 12, "updated_at": "not-a-date"}, note)

    assert result is False
    assert "malformed updated_at" in caplog.text


def test_vault_reconcile_dry_run_update_does_not_mutate_db_or_note(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="vault_sync",
        actor="vault_sync",
    )
    memory = svc.get_memory(memory.id)
    conn.close()
    original_note = (
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        f"memory_id: {memory.id}\n"
        f"sync_base_updated_at: \"{memory.updated_at}\"\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n"
    )
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(note, original_note)

    result = get_tool(server, "vault_reconcile_memories").fn(True)

    assert result["success"] is True
    assert result["data"]["report"]["updated"] == 1
    assert note.read_text(encoding="utf-8") == original_note
    row = _memory_row(db_path, "timezone")
    assert row["updated_at"] == memory.updated_at
    assert json.loads(row["payload_json"])["value"] == "UTC"
    assert _event_types(db_path, memory.id) == ["created", "promoted"]


def test_vault_reconcile_dry_run_confirm_does_not_mutate_db_or_note(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.5,
        payload={"category": "timezone", "value": "UTC"},
        source="detector",
        actor="detector",
    )
    conn.close()
    original_note = (
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        f"memory_id: {memory.id}\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n"
    )
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(note, original_note)

    result = get_tool(server, "vault_reconcile_memories").fn(True)

    assert result["success"] is True
    assert result["data"]["report"]["confirmed"] == 1
    assert note.read_text(encoding="utf-8") == original_note
    row = _memory_row(db_path, "timezone")
    assert row["status"] == "candidate"
    assert json.loads(row["payload_json"])["value"] == "UTC"
    assert _event_types(db_path, memory.id) == ["created"]


def test_vault_reconcile_crash_after_frontmatter_write_rolls_back_db(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path, vault, server = _server(tmp_path)
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    def fail_after_frontmatter(self, doc, resolved_path, frontmatter, memory_id):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(VaultReconciler, "_upsert_vault_index", fail_after_frontmatter)

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is False
    assert _count_rows(db_path, "memories") == 0
    assert _count_rows(db_path, "memory_events") == 0
    assert "memory_id:" in note.read_text(encoding="utf-8")

    monkeypatch.undo()
    retry = get_tool(server, "vault_reconcile_memories").fn(False)

    assert retry["success"] is True
    report = retry["data"]["report"]
    assert report["skipped"] == 1
    assert report["warnings"][0]["kind"] == "missing_memory"
    assert _count_rows(db_path, "memories") == 0


def test_vault_reconcile_crash_after_update_frontmatter_retries_as_conflict(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path, vault, server = _server(tmp_path)
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="vault_sync",
        actor="vault_sync",
    )
    conn.execute(
        "UPDATE memories SET updated_at = '2000-01-01 00:00:00' WHERE id = ?",
        (memory.id,),
    )
    conn.commit()
    conn.close()
    note = vault / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        f"memory_id: {memory.id}\n"
        "sync_base_updated_at: \"2000-01-01 00:00:00\"\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    def fail_after_frontmatter(self, doc, resolved_path, frontmatter, memory_id):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(VaultReconciler, "_upsert_vault_index", fail_after_frontmatter)

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is False
    row_after_crash = _memory_row(db_path, "timezone")
    assert row_after_crash["updated_at"] == "2000-01-01 00:00:00"
    assert json.loads(row_after_crash["payload_json"])["value"] == "UTC"
    assert 'sync_base_updated_at: "2000-01-01 00:00:00"' not in note.read_text(
        encoding="utf-8"
    )

    monkeypatch.undo()
    retry = get_tool(server, "vault_reconcile_memories").fn(False)

    assert retry["success"] is True
    report = retry["data"]["report"]
    assert report["skipped"] == 1
    assert report["conflicts"] == 1
    warning = report["warnings"][0]
    assert warning["kind"] == "conflict"
    assert warning["db_updated_at"] == "2000-01-01 00:00:00"
    assert warning["sync_base_updated_at"] != "2000-01-01 00:00:00"
    assert json.loads(_memory_row(db_path, "timezone")["payload_json"])["value"] == "UTC"


def test_vault_reconcile_dry_run_does_not_mutate_db_or_note(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    note = vault / "Minx" / "Memory" / "timezone.md"
    original_note = (
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"category\":\"timezone\",\"value\":\"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n"
    )
    _write(note, original_note)

    result = get_tool(server, "vault_reconcile_memories").fn(True)

    assert result["success"] is True
    assert result["data"]["report"]["created"] == 1
    assert note.read_text(encoding="utf-8") == original_note
    conn = get_connection(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 0
    finally:
        conn.close()


def test_vault_reconcile_invalid_note_does_not_block_later_notes(tmp_path: Path) -> None:
    db_path, vault, server = _server(tmp_path)
    _write(
        vault / "Minx" / "Memory" / "bad.md",
        "---\n"
        "type: minx-memory\n"
        "  nested: nope\n"
        "---\n",
    )
    _write(
        vault / "Minx" / "Memory" / "timezone.md",
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "payload_json: '{\"category\":\"timezone\",\"value\":\"America/Chicago\"}'\n"
        "---\n"
        "# Timezone\n",
    )

    result = get_tool(server, "vault_reconcile_memories").fn(False)

    assert result["success"] is True
    report = result["data"]["report"]
    assert report["scanned"] == 2
    assert report["applied"] == 1
    assert report["skipped"] == 1
    assert report["warnings"][0]["kind"] == "invalid_note"
    assert _memory_row(db_path, "timezone")["status"] == "active"
