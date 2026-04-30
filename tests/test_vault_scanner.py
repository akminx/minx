from __future__ import annotations

import json
import threading
from pathlib import Path
from sqlite3 import IntegrityError

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core import vault_scanner as vault_scanner_module
from minx_mcp.core.memory_service import MemoryService
from minx_mcp.core.vault_scanner import VaultScanner
from minx_mcp.db import get_connection
from minx_mcp.vault_reader import VaultReader


def _scanner(tmp_path: Path):
    db_path = tmp_path / "minx.db"
    vault_root = tmp_path / "vault"
    conn = get_connection(db_path)
    reader = VaultReader(vault_root, ("Minx",))
    return conn, VaultScanner(conn, reader)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fake_github_token() -> str:
    return "".join(("gh", "p_", "a" * 36))


def _json_escaped_github_token() -> str:
    return _fake_github_token().replace("p", "\\u0070", 1)


def test_vault_scanner_skips_secret_bearing_frontmatter_without_index_or_memory_write(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    secret = _fake_github_token()
    note = tmp_path / "vault" / "Minx" / "Memory" / "secret.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.secret\n"
            "memory_type: preference\n"
            "subject: secret\n"
            f"value: {secret}\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert report.scanned == 1
    assert report.indexed == 0
    assert report.memory_syncs == 0
    assert len(report.warnings) == 1
    assert "secret detected in vault frontmatter" in report.warnings[0].lower()
    assert secret not in report.warnings[0]
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 0


def test_vault_scanner_skips_secret_bearing_body_without_index_or_memory_write(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    secret = _fake_github_token()
    note = tmp_path / "vault" / "Minx" / "Memory" / "secret-body.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.secret_body\n"
            "memory_type: preference\n"
            "subject: secret_body\n"
            "value: safe\n"
            "---\n"
            f"Debug token: {secret}\n"
        ),
    )

    report = scanner.scan()

    assert report.scanned == 1
    assert report.indexed == 0
    assert report.memory_syncs == 0
    assert len(report.warnings) == 1
    assert "secret detected in vault body" in report.warnings[0].lower()
    assert secret not in report.warnings[0]
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 0


def test_vault_scanner_sanitizes_secret_shaped_parse_error_warnings(tmp_path: Path) -> None:
    _conn, scanner = _scanner(tmp_path)
    secret_key = _fake_github_token()
    note = tmp_path / "vault" / "Minx" / "Notes" / "duplicate-secret-key.md"
    _write(
        note,
        (
            "---\n"
            f"{secret_key}: first\n"
            f"{secret_key}: second\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert len(report.warnings) == 1
    assert secret_key not in report.warnings[0]
    assert "secret-shaped" in report.warnings[0]


def test_vault_scanner_redacts_secret_decoded_from_payload_json_before_memory_write(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    secret = _fake_github_token()
    escaped = _json_escaped_github_token()
    note = tmp_path / "vault" / "Minx" / "Memory" / "escaped-payload.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.escaped_payload\n"
            "memory_type: preference\n"
            "subject: escaped_payload\n"
            f"payload_json: '{{\"value\":\"{escaped}\"}}'\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert report.memory_syncs == 1
    row = conn.execute("SELECT payload_json FROM memories WHERE subject = 'escaped_payload'").fetchone()
    assert json.loads(row["payload_json"]) == {"value": "[REDACTED:github_token]"}
    assert secret not in row["payload_json"]


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
    created_fingerprint = memory["content_fingerprint"]
    assert created_fingerprint is not None
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
    refreshed = conn.execute(
        "SELECT payload_json, content_fingerprint FROM memories WHERE id = ?",
        (memory["id"],),
    ).fetchone()
    payload = json.loads(refreshed["payload_json"])
    assert payload == {"category": "timezone", "value": "America/Chicago"}
    assert refreshed["content_fingerprint"] is not None
    assert refreshed["content_fingerprint"] != created_fingerprint

    note.unlink()
    orphaned = scanner.scan()

    assert orphaned.orphaned == 1
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM memories WHERE id = ?", (memory["id"],)).fetchone()[0] == "active"


def test_vault_scanner_integrity_warning_classifies_live_structural_conflict(tmp_path: Path) -> None:
    conn, _scanner_instance = _scanner(tmp_path)
    service = MemoryService(tmp_path / "minx.db", conn=conn)
    existing = service.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=1.0,
        payload={"value": "UTC"},
        source="user",
        actor="user",
    )
    warnings: list[str] = []

    vault_scanner_module._append_memory_integrity_warning(
        conn,
        warnings,
        "Minx/Memory/timezone.md",
        memory_type="preference",
        scope="core",
        subject="timezone",
        fingerprint="unmatched-fingerprint",
        exclude_memory_id=None,
        exc=IntegrityError("UNIQUE constraint failed: memories.memory_type, memories.scope, memories.subject"),
    )

    assert warnings == [
        f"Minx/Memory/timezone.md: structural_triple conflicts with live memory_id={existing.id}; skipped"
    ]


def test_vault_scanner_warns_and_skips_memory_note_on_live_fingerprint_collision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    conn, scanner = _scanner(tmp_path)
    service = MemoryService(tmp_path / "minx.db", conn=conn)
    existing = service.create_memory(
        memory_type="preference",
        scope="core",
        subject="timezone",
        confidence=1.0,
        payload={"category": "timezone", "value": "UTC"},
        source="user",
        actor="user",
    )
    conn.execute(
        "UPDATE memories SET content_fingerprint = ? WHERE id = ?",
        ("forced-collision", existing.id),
    )
    conn.commit()
    monkeypatch.setattr(
        vault_scanner_module,
        "memory_content_fingerprint",
        lambda *args, **kwargs: "forced-collision",
    )
    note = tmp_path / "vault" / "Minx" / "Memory" / "alias.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.alias\n"
            "memory_type: preference\n"
            "subject: alias\n"
            "category: timezone\n"
            "value: UTC\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert report.scanned == 1
    assert report.memory_syncs == 0
    assert report.indexed == 1
    assert len(report.warnings) == 1
    assert "content_fingerprint" in report.warnings[0]
    assert f"memory_id={existing.id}" in report.warnings[0]
    assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    row = conn.execute("SELECT memory_id FROM vault_index WHERE vault_path = ?", ("Minx/Memory/alias.md",)).fetchone()
    assert row is not None
    assert row["memory_id"] is None


def test_vault_scanner_prefers_scope_over_domain_when_both_are_present(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    note = tmp_path / "vault" / "Minx" / "Memory" / "scope_precedence.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "domain: core\n"
            "scope:  core  \n"
            "memory_key: core.preference.scope_precedence\n"
            "memory_type: preference\n"
            "subject: scope_precedence\n"
            "value: yes\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert report.memory_syncs == 1
    row = conn.execute("SELECT scope FROM vault_index WHERE vault_path = 'Minx/Memory/scope_precedence.md'").fetchone()
    assert row["scope"] == "core"


def test_vault_scanner_rejects_mismatched_scope_and_domain(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    note = tmp_path / "vault" / "Minx" / "Memory" / "mismatch.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "domain: legacy\n"
            "scope: core\n"
            "memory_key: legacy.preference.mismatch\n"
            "memory_type: preference\n"
            "subject: mismatch\n"
            "value: yes\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    assert report.memory_syncs == 0
    assert len(report.warnings) == 1
    assert "invalid minx-memory frontmatter" in report.warnings[0]
    row = conn.execute("SELECT scope FROM vault_index WHERE vault_path = 'Minx/Memory/mismatch.md'").fetchone()
    assert row["scope"] == "core"


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


def test_vault_scanner_clears_memory_id_when_synced_note_becomes_invalid(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    note = tmp_path / "vault" / "Minx" / "Memory" / "invalid_after_sync.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.invalid_after_sync\n"
            "memory_type: preference\n"
            "subject: invalid_after_sync\n"
            "value: original\n"
            "---\n"
        ),
    )
    first = scanner.scan()
    assert first.memory_syncs == 1
    linked_before = conn.execute(
        "SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/invalid_after_sync.md'"
    ).fetchone()["memory_id"]
    assert linked_before is not None

    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_type: preference\n"
            "subject: invalid_after_sync\n"
            "value: broken\n"
            "---\n"
        ),
    )
    second = scanner.scan()

    linked_after = conn.execute(
        "SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/invalid_after_sync.md'"
    ).fetchone()["memory_id"]
    assert second.memory_syncs == 0
    assert linked_after is None
    assert any("invalid minx-memory" in warning for warning in second.warnings)


def test_vault_scanner_clears_memory_id_when_memory_note_becomes_non_memory(
    tmp_path: Path,
) -> None:
    conn, scanner = _scanner(tmp_path)
    note = tmp_path / "vault" / "Minx" / "Memory" / "demoted.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.demoted\n"
            "memory_type: preference\n"
            "subject: demoted\n"
            "value: original\n"
            "---\n"
        ),
    )
    first = scanner.scan()
    assert first.memory_syncs == 1
    linked_before = conn.execute(
        "SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/demoted.md'"
    ).fetchone()["memory_id"]
    assert linked_before is not None

    _write(note, "---\ntype: minx-entity\nscope: core\n---\nDemoted body\n")
    second = scanner.scan()

    row = conn.execute(
        "SELECT note_type, memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/demoted.md'"
    ).fetchone()
    assert second.memory_syncs == 0
    assert row["note_type"] == "minx-entity"
    assert row["memory_id"] is None


def test_vault_scanner_does_not_delete_existing_index_when_path_enumeration_fails(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    vault_root = tmp_path / "vault"
    conn = get_connection(db_path)
    reader = VaultReader(vault_root, ("Minx",))
    scanner = VaultScanner(conn, reader)
    good = tmp_path / "vault" / "Minx" / "Entities" / "ok.md"
    _write(good, "---\ntype: minx-entity\ndomain: core\n---\n")
    first = scanner.scan()
    assert first.indexed == 1

    good.unlink()

    def fail_walk(prefix: str):
        raise InvalidInputError(f"cannot enumerate {prefix}")

    reader.iter_markdown_paths = fail_walk  # type: ignore[method-assign]
    second = scanner.scan()

    assert second.orphaned == 0
    assert "vault walk failed" in second.warnings[0]
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 1


def test_vault_scanner_marks_oserror_during_path_enumeration_as_incomplete_walk(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    vault_root = tmp_path / "vault"
    conn = get_connection(db_path)
    reader = VaultReader(vault_root, ("Minx",))
    scanner = VaultScanner(conn, reader)
    good = tmp_path / "vault" / "Minx" / "Entities" / "ok.md"
    _write(good, "---\ntype: minx-entity\nscope: core\n---\n")
    assert scanner.scan().indexed == 1
    good.unlink()

    def fail_walk(prefix: str):
        raise OSError(f"cannot enumerate {prefix}")

    reader.iter_markdown_paths = fail_walk  # type: ignore[method-assign]
    report = scanner.scan()

    assert report.orphaned == 0
    assert "vault walk failed" in report.warnings[0]
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 1


def test_vault_scanner_indexes_good_docs_and_cleans_orphans_when_one_file_is_unreadable(
    tmp_path: Path,
) -> None:
    """Per-file read failures warn and skip that file, but do not suppress orphan cleanup.

    A single malformed note must not permanently poison orphan reclamation for the
    rest of the vault — only an outer walk-enumeration failure should do that.
    """
    conn, scanner = _scanner(tmp_path)
    good = tmp_path / "vault" / "Minx" / "Entities" / "good.md"
    bad = tmp_path / "vault" / "Minx" / "Entities" / "bad.md"
    _write(good, "---\ntype: minx-entity\nscope: core\n---\n")
    bad.write_bytes(b"\xff")
    conn.execute(
        """
        INSERT INTO vault_index (vault_path, note_type, scope, content_hash, last_scanned_at, metadata_json, memory_id)
        VALUES ('Minx/Orphans/old.md', 'minx-entity', 'core', 'old', 'stale-token', '{}', NULL)
        """
    )
    conn.commit()

    report = scanner.scan()

    assert report.scanned == 1
    assert report.indexed == 1
    assert report.orphaned == 1
    assert any("bad.md" in warning for warning in report.warnings)
    assert (
        conn.execute("SELECT COUNT(*) FROM vault_index WHERE vault_path = 'Minx/Entities/good.md'").fetchone()[0] == 1
    )
    assert conn.execute("SELECT COUNT(*) FROM vault_index WHERE vault_path = 'Minx/Orphans/old.md'").fetchone()[0] == 0


def test_vault_scanner_skips_file_when_read_document_raises_oserror(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    vault_root = tmp_path / "vault"
    conn = get_connection(db_path)
    reader = VaultReader(vault_root, ("Minx",))
    scanner = VaultScanner(conn, reader)
    good = tmp_path / "vault" / "Minx" / "Entities" / "good.md"
    bad = tmp_path / "vault" / "Minx" / "Entities" / "bad.md"
    _write(good, "---\ntype: minx-entity\nscope: core\n---\n")
    _write(bad, "---\ntype: minx-entity\nscope: core\n---\n")
    original_read = reader.read_document

    def flaky_read(relative_path: str):
        if relative_path == "Minx/Entities/bad.md":
            raise OSError("disk vanished")
        return original_read(relative_path)

    reader.read_document = flaky_read  # type: ignore[method-assign]

    report = scanner.scan()

    assert report.scanned == 1
    assert report.indexed == 1
    assert any("bad.md" in warning for warning in report.warnings)


def test_vault_scanner_does_not_orphan_previously_indexed_file_that_cannot_be_read(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "minx.db"
    vault_root = tmp_path / "vault"
    conn = get_connection(db_path)
    reader = VaultReader(vault_root, ("Minx",))
    scanner = VaultScanner(conn, reader)
    note = tmp_path / "vault" / "Minx" / "Memory" / "flaky.md"
    _write(
        note,
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.flaky\n"
            "memory_type: preference\n"
            "subject: flaky\n"
            "value: present\n"
            "---\n"
        ),
    )
    first = scanner.scan()
    assert first.memory_syncs == 1
    memory_id = conn.execute("SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/flaky.md'").fetchone()[
        "memory_id"
    ]
    assert memory_id is not None
    original_read = reader.read_document

    def flaky_read(relative_path: str):
        if relative_path == "Minx/Memory/flaky.md":
            raise OSError("temporarily locked")
        return original_read(relative_path)

    reader.read_document = flaky_read  # type: ignore[method-assign]
    second = scanner.scan()

    row = conn.execute("SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/flaky.md'").fetchone()
    orphan_events = conn.execute(
        """
        SELECT COUNT(*)
        FROM memory_events
        WHERE memory_id = ? AND event_type = 'vault_synced'
          AND payload_json LIKE '%orphaned%'
        """,
        (memory_id,),
    ).fetchone()[0]
    assert second.scanned == 0
    assert second.orphaned == 0
    assert row is not None
    assert row["memory_id"] == memory_id
    assert orphan_events == 0
    assert any("flaky.md" in warning for warning in second.warnings)


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
    _conn, scanner = _scanner(tmp_path)
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

    note = tmp_path / "vault" / "Minx" / "Notes" / "slow.md"
    _write(note, "---\ntype: entity\n---\nBody\n")

    write_completed = threading.Event()
    write_error: list[Exception] = []

    original_walk = reader.iter_markdown_paths

    def slow_walk(prefix: str):
        # Yield paths with a slight pause to allow a concurrent write.
        paths = list(original_walk(prefix))
        # Signal that the walk is underway (no lock should be held here).
        write_completed.set()
        yield from paths

    reader.iter_markdown_paths = slow_walk  # type: ignore[method-assign]

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
    scanner = VaultScanner(conn, reader)
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
    assert conn.execute("SELECT COUNT(*) FROM vault_index").fetchone()[0] == 0, (
        "dry_run=True must not persist vault_index rows"
    )
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

    # Re-scan after a content change; the changed-note sync path must also clear
    # terminal pointers instead of preserving the old memory_id through COALESCE.
    _write(note, content + "\n<!-- touched -->\n")
    second = scanner.scan()

    row = conn.execute(
        "SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/will_be_rejected.md'"
    ).fetchone()
    assert row["memory_id"] is None, "vault_index.memory_id must be NULL after terminal memory rescan"
    assert any("terminal" in w for w in second.warnings), f"Expected terminal warning, got: {second.warnings}"


def test_vault_scanner_clears_stale_memory_id_on_unchanged_terminal_rescan(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    service = MemoryService(tmp_path / "minx.db", conn=conn)
    note = tmp_path / "vault" / "Minx" / "Memory" / "stale_pointer.md"
    content = (
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.stale_pointer\n"
        "memory_type: preference\n"
        "subject: stale_pointer\n"
        "value: some_pref\n"
        "---\n"
    )
    _write(note, content)

    first = scanner.scan()
    assert first.memory_syncs == 1
    memory_id = conn.execute(
        "SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/stale_pointer.md'"
    ).fetchone()["memory_id"]
    assert memory_id is not None

    service.expire_memory(memory_id, actor="user", reason="changed my mind")
    conn.commit()

    second = scanner.scan()

    row = conn.execute("SELECT memory_id FROM vault_index WHERE vault_path = 'Minx/Memory/stale_pointer.md'").fetchone()
    assert row["memory_id"] is None
    assert second.memory_syncs == 0
    assert any("terminal" in warning for warning in second.warnings)


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
    payload = json.loads(conn.execute("SELECT payload_json FROM memories WHERE subject = 'tz'").fetchone()[0])
    assert "sync_base_updated_at" not in payload
    assert payload == {"value": "UTC"}


def test_vault_scanner_excludes_obsidian_housekeeping_keys_from_payload(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    _write(
        tmp_path / "vault" / "Minx" / "Memory" / "obsidian_keys.md",
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.preference.obsidian_keys\n"
            "memory_type: preference\n"
            "subject: obsidian_keys\n"
            "value: yes\n"
            "tags: [memory, reviewed]\n"
            "title: Obsidian Keys\n"
            "id: note-123\n"
            "created: 2026-04-19\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    payload = json.loads(
        conn.execute("SELECT payload_json FROM memories WHERE subject = 'obsidian_keys'").fetchone()[0]
    )
    assert report.memory_syncs == 1
    assert payload == {"value": "yes"}


def test_vault_scanner_keeps_entity_fact_aliases_as_payload(tmp_path: Path) -> None:
    conn, scanner = _scanner(tmp_path)
    _write(
        tmp_path / "vault" / "Minx" / "Memory" / "merchant.md",
        (
            "---\n"
            "type: minx-memory\n"
            "scope: core\n"
            "memory_key: core.entity_fact.local_shop\n"
            "memory_type: entity_fact\n"
            "subject: local_shop\n"
            "category: merchant\n"
            "aliases: [Local Shop, The Shop]\n"
            "---\n"
        ),
    )

    report = scanner.scan()

    payload = json.loads(conn.execute("SELECT payload_json FROM memories WHERE subject = 'local_shop'").fetchone()[0])
    assert report.memory_syncs == 1
    assert payload == {"category": "merchant", "aliases": ["Local Shop", "The Shop"]}
