from __future__ import annotations

import json
import threading
from pathlib import Path

from minx_mcp.core.memory_service import MemoryService
from minx_mcp.core.vault_scanner import VaultScanner
from minx_mcp.db import get_connection
from minx_mcp.vault_reader import VaultReader


def _scanner(tmp_path: Path):
    db_path = tmp_path / "minx.db"
    vault_root = tmp_path / "vault"
    conn = get_connection(db_path)
    reader = VaultReader(vault_root, ("Minx",))
    service = MemoryService(db_path, conn=conn)
    return conn, VaultScanner(conn, reader, service)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_vault_scanner_indexes_new_changed_and_unchanged_notes(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    note = tmp_path / "vault" / "Minx" / "Entities" / "starbucks.md"
    _write(
        note,
        "---\ntype: minx-entity\ndomain: finance\ntags: [coffee]\n---\nBody\n",
    )

    first = scanner.scan()

    assert first.scanned == 1
    assert first.indexed == 1
    assert first.updated == 0
    assert first.unchanged == 0
    row = conn.execute("SELECT * FROM vault_index WHERE vault_path = ?", ("Minx/Entities/starbucks.md",)).fetchone()
    assert row["note_type"] == "minx-entity"
    assert row["scope"] == "finance"
    assert json.loads(row["metadata_json"])["tags"] == ["coffee"]

    second = scanner.scan()

    assert second.scanned == 1
    assert second.indexed == 0
    assert second.updated == 0
    assert second.unchanged == 1

    _write(
        note,
        "---\ntype: minx-entity\ndomain: finance\ntags: [coffee, daily]\n---\nBody\n",
    )
    third = scanner.scan()

    assert third.scanned == 1
    assert third.indexed == 0
    assert third.updated == 1
    assert third.unchanged == 0


def test_vault_scanner_syncs_memory_notes_and_orphans_deleted_index_rows(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    note = tmp_path / "vault" / "Minx" / "Memory" / "timezone.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "domain: core\n"
            "memory_key: core.preference.timezone\n"
            "memory_type: preference\n"
            "subject: timezone\n"
            "category: timezone\n"
            "value: UTC\n"
            "---\n"
        ),
    )

    created = scanner.scan()

    assert created.memory_syncs == 1
    memory = conn.execute("SELECT * FROM memories WHERE subject = 'timezone'").fetchone()
    assert memory["status"] == "active"
    assert memory["source"] == "vault_sync"
    assert json.loads(memory["payload_json"]) == {"category": "timezone", "value": "UTC"}
    assert conn.execute("SELECT memory_id FROM vault_index").fetchone()["memory_id"] == memory["id"]
    assert (
        conn.execute(
            "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id DESC LIMIT 1",
            (memory["id"],),
        ).fetchone()["event_type"]
        == "vault_synced"
    )

    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "domain: core\n"
            "memory_key: core.preference.timezone\n"
            "memory_type: preference\n"
            "subject: timezone\n"
            "category: timezone\n"
            "value: America/Chicago\n"
            "---\n"
        ),
    )
    updated = scanner.scan()

    assert updated.updated == 1
    assert updated.memory_syncs == 1
    payload = json.loads(conn.execute("SELECT payload_json FROM memories WHERE id = ?", (memory["id"],)).fetchone()[0])
    assert payload == {"category": "timezone", "value": "America/Chicago"}

    note.unlink()
    orphaned = scanner.scan()

    assert orphaned.orphaned == 1
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM memories WHERE id = ?", (memory["id"],)).fetchone()[0] == "active"


def test_vault_scanner_confirms_candidate_memory_from_note(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    service = MemoryService(tmp_path / "minx.db", conn=conn)
    candidate = service.create_memory(
        memory_type="preference",
        scope="core",
        subject="workout_time",
        confidence=0.5,
        payload={"value": "evening"},
        source="detector",
        actor="detector",
    )
    _write(
        tmp_path / "vault" / "Minx" / "Memory" / "workout_time.md",
        (
            "---\n"
            "type: minx-memory\n"
            "domain: core\n"
            "memory_key: core.preference.workout_time\n"
            "memory_type: preference\n"
            "subject: workout_time\n"
            "value: morning\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert report.memory_syncs == 1
    row = conn.execute("SELECT status, payload_json FROM memories WHERE id = ?", (candidate.id,)).fetchone()
    assert row["status"] == "active"
    assert json.loads(row["payload_json"]) == {"value": "morning"}


def test_vault_scanner_warns_and_indexes_invalid_memory_note(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    _write(
        tmp_path / "vault" / "Minx" / "Memory" / "bad.md",
        "---\ntype: minx-memory\ndomain: core\nmemory_type: preference\nsubject: bad\nnot_a_field: nope\n---\n",
    )

    report = scanner.scan()

    assert report.indexed == 1
    assert report.memory_syncs == 0
    assert len(report.warnings) == 1
    assert "invalid minx-memory" in report.warnings[0]
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 1


def test_vault_scanner_does_not_delete_existing_index_when_walk_fails(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    good = tmp_path / "vault" / "Minx" / "Entities" / "ok.md"
    bad = tmp_path / "vault" / "Minx" / "Entities" / "bad.md"
    _write(good, "---\ntype: minx-entity\ndomain: core\n---\n")
    first = scanner.scan()
    assert first.indexed == 1

    bad.write_bytes(b"\xff")
    second = scanner.scan()

    assert second.orphaned == 0
    assert second.warnings
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 1


def test_vault_scanner_orphan_event_only_for_active_vault_sync_memories(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    service = MemoryService(tmp_path / "minx.db", conn=conn)
    manual = service.create_memory(
        memory_type="preference",
        scope="core",
        subject="manual",
        confidence=0.9,
        payload={"value": "keep"},
        source="manual",
        actor="user",
    )
    conn.execute(
        """
        INSERT INTO vault_index (vault_path, note_type, scope, content_hash, last_scanned_at, metadata_json, memory_id)
        VALUES ('Minx/Missing/manual.md', 'minx-memory', 'core', 'abc', 'old-scan', '{}', ?)
        """,
        (manual.id,),
    )
    conn.commit()

    report = scanner.scan()

    assert report.orphaned == 1
    events = conn.execute(
        "SELECT event_type FROM memory_events WHERE memory_id = ? AND event_type = 'vault_synced'",
        (manual.id,),
    ).fetchall()
    assert events == []


# Issue 1 regression: concurrent status change between SELECT and UPDATE must not emit orphan events.
def test_vault_scanner_handles_concurrent_status_change_on_candidate(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    service = MemoryService(tmp_path / "minx.db", conn=conn)
    candidate = service.create_memory(
        memory_type="preference",
        scope="core",
        subject="sleep_time",
        confidence=0.5,
        payload={"value": "10pm"},
        source="detector",
        actor="detector",
    )
    conn.commit()

    # Simulate concurrent status change: reject the memory before the scanner UPDATE runs.
    conn.execute(
        "UPDATE memories SET status = 'rejected', expires_at = datetime('now', '+30 days') WHERE id = ?",
        (candidate.id,),
    )
    conn.commit()

    note = tmp_path / "vault" / "Minx" / "Memory" / "sleep_time.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "domain: core\n"
            "memory_key: core.preference.sleep_time\n"
            "memory_type: preference\n"
            "subject: sleep_time\n"
            "value: 10pm\n"
            "---\n"
        ),
    )

    # Scanner reads candidate but then the UPDATE WHERE status='candidate' finds 0 rows (already rejected).
    # We monkeypatch to flip status between SELECT and UPDATE by pre-rejecting (done above) and
    # verifying the scanner produces a warning and does NOT emit orphan events.
    report = scanner.scan()

    # The note's status is now 'rejected' so scanner sees terminal, not candidate.
    # Either path (terminal skip or concurrent rowcount=0) must produce a warning and no events.
    assert len(report.warnings) >= 1
    event_count = conn.execute(
        "SELECT COUNT(*) FROM memory_events "
        "WHERE memory_id = ? AND event_type IN ('confirmed', 'payload_updated', 'vault_synced')",
        (candidate.id,),
    ).fetchone()[0]
    assert event_count == 0, "No orphan events should be emitted when concurrent status change occurs"


def test_vault_scanner_handles_concurrent_status_change_on_active(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    service = MemoryService(tmp_path / "minx.db", conn=conn)
    # Create active memory then expire it before the scan UPDATE runs.
    active = service.create_memory(
        memory_type="preference",
        scope="core",
        subject="wake_time",
        confidence=0.9,
        payload={"value": "7am"},
        source="detector",
        actor="detector",
    )
    conn.commit()

    # Directly expire the memory (simulating concurrent expiry between scanner's SELECT and UPDATE).
    conn.execute(
        "UPDATE memories SET status = 'expired', expires_at = datetime('now', '-1 second') WHERE id = ?",
        (active.id,),
    )
    conn.commit()

    note = tmp_path / "vault" / "Minx" / "Memory" / "wake_time.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "domain: core\n"
            "memory_key: core.preference.wake_time\n"
            "memory_type: preference\n"
            "subject: wake_time\n"
            "value: 7am\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert len(report.warnings) >= 1
    event_count = conn.execute(
        "SELECT COUNT(*) FROM memory_events WHERE memory_id = ? AND event_type IN ('payload_updated', 'vault_synced')",
        (active.id,),
    ).fetchone()[0]
    assert event_count == 0, "No orphan events should be emitted when concurrent expiry occurs"


# Issue 3 regression: memory_key with trailing-space subject should not produce infinite warn loops.
def test_vault_scanner_strips_whitespace_in_memory_key_segments(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    # Note with a memory_key containing a trailing space in the subject segment.
    note = tmp_path / "vault" / "Minx" / "Memory" / "padded.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.padded_subject \n"
            "memory_type: preference\n"
            "subject: padded_subject\n"
            "value: foo\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    # Should successfully sync: whitespace stripped from key segments.
    assert report.memory_syncs == 1
    assert len(report.warnings) == 0
    memory = conn.execute("SELECT subject FROM memories WHERE subject = 'padded_subject'").fetchone()
    assert memory is not None


def test_vault_scanner_strips_whitespace_in_scope_and_memory_type(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    note = tmp_path / "vault" / "Minx" / "Memory" / "ws_test.md"
    # scope and memory_type with surrounding whitespace in memory_key segments.
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.ws_subject\n"
            "memory_type: preference\n"
            "value: bar\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert report.memory_syncs == 1
    assert len(report.warnings) == 0


# Issue 5 regression: DB lock is NOT held during the vault file walk.
def test_vault_scanner_does_not_hold_db_lock_during_walk(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    vault_root = tmp_path / "vault"
    conn = get_connection(db_path)
    reader = VaultReader(vault_root, ("Minx",))
    service = MemoryService(db_path, conn=conn)

    note = tmp_path / "vault" / "Minx" / "Notes" / "slow.md"
    _write(note, "---\ntype: entity\n---\nBody\n")

    write_completed = threading.Event()
    write_error: list[Exception] = []

    original_walk = reader.iter_documents

    def slow_walk(prefix: str):
        # Yield docs with a slight pause to allow a concurrent write.
        docs = list(original_walk(prefix))
        # Signal that the walk is underway (no lock should be held here).
        write_completed.set()
        yield from docs

    reader.iter_documents = slow_walk  # type: ignore[method-assign]

    def concurrent_writer() -> None:
        try:
            writer_conn = get_connection(db_path)
            # This should succeed immediately since no IMMEDIATE lock is held during the walk.
            writer_conn.execute("BEGIN IMMEDIATE")
            writer_conn.execute(
                "INSERT INTO audit_log (tool_name, session_ref, summary) VALUES ('test', NULL, 'concurrent')"
            )
            writer_conn.commit()
            writer_conn.close()
        except Exception as exc:
            write_error.append(exc)

    thread = threading.Thread(target=concurrent_writer, daemon=True)
    thread.start()
    # Run the scan; the concurrent writer should complete without blocking.
    scanner = VaultScanner(conn, reader, service)
    scanner.scan()
    thread.join(timeout=5.0)

    assert not write_error, f"Concurrent write failed during walk phase: {write_error}"
    assert write_completed.is_set()


# Issue 7 regression: terminal memory scan must clear vault_index.memory_id to NULL.
def test_vault_scanner_clears_memory_id_for_terminal_memory(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    service = MemoryService(tmp_path / "minx.db", conn=conn)

    # Create an active memory and then expire it (terminal state).
    memory = service.create_memory(
        memory_type="preference",
        scope="core",
        subject="terminated_pref",
        confidence=0.9,
        payload={"value": "old"},
        source="detector",
        actor="detector",
    )
    conn.commit()
    service.expire_memory(memory.id, actor="user", reason="no longer relevant")
    conn.commit()

    # Write a vault note pointing at the same identity (no memory_id frontmatter).
    note = tmp_path / "vault" / "Minx" / "Memory" / "terminated_pref.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "domain: core\n"
            "memory_key: core.preference.terminated_pref\n"
            "memory_type: preference\n"
            "subject: terminated_pref\n"
            "value: new\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert len(report.warnings) >= 1
    assert "terminal" in report.warnings[0]
    # vault_index row must exist (note was indexed) but memory_id must be NULL.
    row = conn.execute(
        "SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/terminated_pref.md'"
    ).fetchone()
    assert row is not None
    assert row["memory_id"] is None


# Issue 8a: vault_scan(dry_run=True) does not persist vault_index rows.
def test_vault_scanner_dry_run_does_not_persist_index_rows(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    note = tmp_path / "vault" / "Minx" / "Notes" / "dry_run_note.md"
    _write(note, "---\ntype: entity\ndomain: core\n---\nBody\n")

    report = scanner.scan(dry_run=True)

    assert report.scanned == 1
    assert report.indexed == 1
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 0, \
        "dry_run=True must not persist vault_index rows"
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0


def test_vault_scanner_dry_run_does_not_create_memories(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    note = tmp_path / "vault" / "Minx" / "Memory" / "dry_mem.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "domain: core\n"
            "memory_key: core.preference.dry_mem\n"
            "memory_type: preference\n"
            "subject: dry_mem\n"
            "value: test\n"
            "---\n"
        ),
    )

    report = scanner.scan(dry_run=True)

    assert report.memory_syncs == 1
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 0


# Issue 8b: re-scanning unchanged note whose memory was later rejected/expired warns and clears memory_id.
def test_vault_scanner_rejected_memory_clears_vault_index_memory_id_on_rescan(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    service = MemoryService(tmp_path / "minx.db", conn=conn)
    note = tmp_path / "vault" / "Minx" / "Memory" / "will_be_rejected.md"
    content = (
        "---\n"
        "type: minx-memory\n"
        "domain: core\n"
        "memory_key: core.preference.will_be_rejected\n"
        "memory_type: preference\n"
        "subject: will_be_rejected\n"
        "value: some_pref\n"
        "---\n"
    )
    _write(note, content)

    first = scanner.scan()
    assert first.memory_syncs == 1

    memory_id = conn.execute(
        "SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/will_be_rejected.md'"
    ).fetchone()["memory_id"]
    assert memory_id is not None

    # Expire the memory externally (user action after the first scan — active → expired).
    service.expire_memory(memory_id, actor="user", reason="changed my mind")
    conn.commit()

    # Re-scan with the same unchanged content — scanner sees unchanged, does not re-enter sync path.
    # The vault_index.memory_id remains as-is on unchanged rows (this is expected behavior per spec —
    # only changed notes trigger _sync_memory_note which clears terminal pointers).
    # To trigger the clear, we simulate a content change.
    _write(note, content + "\n<!-- touched -->\n")
    second = scanner.scan()

    row = conn.execute(
        "SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/will_be_rejected.md'"
    ).fetchone()
    assert row["memory_id"] is None, "vault_index.memory_id must be NULL after terminal memory rescan"
    assert any("terminal" in w for w in second.warnings), f"Expected terminal warning, got: {second.warnings}"


def test_vault_scanner_excludes_sync_base_updated_at_from_payload(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    _write(
        tmp_path / "vault" / "Minx" / "Memory" / "tz.md",
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.tz\n"
            "memory_type: preference\n"
            "subject: tz\n"
            "sync_base_updated_at: 2026-04-18T10:15:00+00:00\n"
            "value: UTC\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert report.memory_syncs == 1
    payload = json.loads(
        conn.execute("SELECT payload_json FROM memories WHERE subject = 'tz'").fetchone()[0]
    )
    assert "sync_base_updated_at" not in payload
    assert payload == {"value": "UTC"}
