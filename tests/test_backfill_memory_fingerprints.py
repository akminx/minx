"""Tests for the Slice 6g backfill script (spec §10.4).

Coverage matches the spec's test plan:

- Clean no-op on a fully-fingerprinted DB.
- Legacy rows (NULL fingerprints) get fingerprints.
- Live-vs-live collision leaves both rows NULL and exits 2.
- Live-vs-rejected collision writes both fingerprints (rejected rows
  do not compete on the partial unique index).
- Idempotence: second run is a no-op (rows_written == 0).
- ``--force`` overwrites stale stored fingerprints.
- Interrupt safety: ``BaseException`` mid-pass rolls back and does
  not leak the writer lock.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from minx_mcp.core.memory_service import MemoryService
from minx_mcp.db import get_connection
from scripts.backfill_memory_fingerprints import _run_backfill, main


def _fresh_service(tmp_path: Path) -> MemoryService:
    db_path = tmp_path / "backfill.db"
    get_connection(db_path).close()
    return MemoryService(db_path)


def _seed_preference(svc: MemoryService, subject: str, value: str, confidence: float = 0.9) -> int:
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject=subject,
        confidence=confidence,
        payload={"value": value},
        source="user",
        actor="user",
    )
    return rec.id


def _null_fingerprints(svc: MemoryService, *row_ids: int) -> None:
    placeholders = ",".join("?" for _ in row_ids)
    svc.conn.execute(
        f"UPDATE memories SET content_fingerprint = NULL WHERE id IN ({placeholders})",  # noqa: S608
        row_ids,
    )
    svc.conn.commit()


def _fingerprint_of(conn: sqlite3.Connection, row_id: int) -> str | None:
    row = conn.execute(
        "SELECT content_fingerprint FROM memories WHERE id = ?", (row_id,)
    ).fetchone()
    if row is None:
        return None
    return None if row["content_fingerprint"] is None else str(row["content_fingerprint"])


def test_backfill_noop_on_fully_fingerprinted_db(tmp_path: Path) -> None:
    svc = _fresh_service(tmp_path)
    for i, subj in enumerate(["a", "b", "c"]):
        _seed_preference(svc, subj, str(i))
    before_fps = [
        str(r["content_fingerprint"])
        for r in svc.conn.execute("SELECT content_fingerprint FROM memories ORDER BY id").fetchall()
    ]
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    rc = main([str(db_path)])
    assert rc == 0

    conn = get_connection(db_path)
    try:
        after_fps = [
            str(r["content_fingerprint"])
            for r in conn.execute("SELECT content_fingerprint FROM memories ORDER BY id").fetchall()
        ]
    finally:
        conn.close()
    assert after_fps == before_fps


def test_backfill_populates_null_fingerprints(tmp_path: Path) -> None:
    svc = _fresh_service(tmp_path)
    ids = [_seed_preference(svc, f"s{i}", str(i)) for i in range(3)]
    _null_fingerprints(svc, *ids)
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    rc = main([str(db_path)])
    assert rc == 0

    conn = get_connection(db_path)
    try:
        for i in ids:
            fp = _fingerprint_of(conn, i)
            assert fp is not None and len(fp) == 64
    finally:
        conn.close()


def test_backfill_live_vs_live_collision_leaves_both_null_and_exits_2(tmp_path: Path) -> None:
    svc = _fresh_service(tmp_path)
    # Insert two rows manually that would both collide on content fingerprint.
    # Bypass the service to mimic a pre-6g DB (where such rows could have been
    # committed because the partial unique index didn't exist yet).
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status,
            payload_json, source, reason, created_at, updated_at
        ) VALUES ('preference', 'core', 'Netflix', 0.9, 'active',
                  '{"value":"yes"}', 'user', '', datetime('now'), datetime('now'))
        """
    )
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status,
            payload_json, source, reason, created_at, updated_at
        ) VALUES ('preference', 'core', 'netflix', 0.9, 'active',
                  '{"value":"yes"}', 'user', '', datetime('now'), datetime('now'))
        """
    )
    svc.conn.commit()
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    rc = main([str(db_path)])
    assert rc == 2

    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, content_fingerprint FROM memories ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert all(r["content_fingerprint"] is None for r in rows)


def test_backfill_live_vs_rejected_both_get_fingerprints(tmp_path: Path) -> None:
    svc = _fresh_service(tmp_path)
    rec = _seed_preference(svc, "Netflix", "yes", confidence=0.5)
    svc.reject_memory(rec, reason="not relevant", actor="user")
    # Second row: different triple, same content, active. The two would share
    # a normalized fingerprint, but the rejected row is excluded from the
    # partial unique index — so both can coexist, and both must end up with
    # a fingerprint so §7.2's rejected-prior check works.
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status,
            payload_json, source, reason, created_at, updated_at
        ) VALUES ('preference', 'core', 'hulu', 0.9, 'active',
                  '{"value":"yes"}', 'user', '', datetime('now'), datetime('now'))
        """
    )
    svc.conn.commit()
    _null_fingerprints(svc, *[r["id"] for r in svc.conn.execute("SELECT id FROM memories").fetchall()])
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    rc = main([str(db_path)])
    assert rc == 0

    conn = get_connection(db_path)
    try:
        for r in conn.execute("SELECT id, content_fingerprint FROM memories").fetchall():
            assert r["content_fingerprint"] is not None
    finally:
        conn.close()


def test_backfill_is_idempotent(tmp_path: Path) -> None:
    svc = _fresh_service(tmp_path)
    for i, subj in enumerate(["a", "b"]):
        _seed_preference(svc, subj, str(i))
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    assert main([str(db_path)]) == 0
    conn = get_connection(db_path)
    try:
        before = {
            int(r["id"]): r["content_fingerprint"]
            for r in conn.execute("SELECT id, content_fingerprint FROM memories").fetchall()
        }
    finally:
        conn.close()

    assert main([str(db_path)]) == 0
    conn = get_connection(db_path)
    try:
        after = {
            int(r["id"]): r["content_fingerprint"]
            for r in conn.execute("SELECT id, content_fingerprint FROM memories").fetchall()
        }
    finally:
        conn.close()
    assert before == after


def test_backfill_rerun_with_partial_fingerprints_detects_collision(tmp_path: Path) -> None:
    """§10.4 v4 fix: if row A has fp F and row B has NULL but same content,
    a re-run must not try to set B's fp to F (would violate the partial
    unique index). Instead it records a collision and exits 2.
    """
    svc = _fresh_service(tmp_path)
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status,
            payload_json, source, reason, created_at, updated_at, content_fingerprint
        ) VALUES ('preference', 'core', 'Netflix', 0.9, 'active',
                  '{"value":"yes"}', 'user', '', datetime('now'), datetime('now'),
                  'manually_set_fingerprint_that_we_do_not_care_about')
        """
    )
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status,
            payload_json, source, reason, created_at, updated_at, content_fingerprint
        ) VALUES ('preference', 'core', 'netflix', 0.9, 'active',
                  '{"value":"yes"}', 'user', '', datetime('now'), datetime('now'),
                  NULL)
        """
    )
    svc.conn.commit()
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    rc = main([str(db_path)])
    assert rc == 2  # collision detected, both rows in same bucket


def test_backfill_force_overwrites_stale_fingerprints(tmp_path: Path) -> None:
    svc = _fresh_service(tmp_path)
    rec_id = _seed_preference(svc, "Netflix", "yes")
    # Poison the stored fingerprint so it disagrees with payload_json.
    svc.conn.execute(
        "UPDATE memories SET content_fingerprint = ? WHERE id = ?",
        ("0" * 64, rec_id),
    )
    svc.conn.commit()
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    # Without --force: skip, exit 2.
    assert main([str(db_path)]) == 2
    conn = get_connection(db_path)
    try:
        assert _fingerprint_of(conn, rec_id) == "0" * 64
    finally:
        conn.close()

    # With --force: overwrite, exit 0.
    assert main([str(db_path), "--force"]) == 0
    conn = get_connection(db_path)
    try:
        fp = _fingerprint_of(conn, rec_id)
        assert fp is not None and fp != "0" * 64
    finally:
        conn.close()


class _FlakyConnection:
    """Thin proxy that raises ``KeyboardInterrupt`` on the first UPDATE.

    ``sqlite3.Connection`` attributes are read-only slots, so we can't
    ``mock.patch.object(conn, "execute", ...)``. Instead we wrap the real
    connection in a proxy and forward everything that the script uses.
    """

    __slots__ = ("_calls", "_inner")

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner
        self._calls = 0

    def execute(self, sql: str, *args, **kwargs):
        if "UPDATE memories SET content_fingerprint" in sql:
            self._calls += 1
            if self._calls == 1:
                raise KeyboardInterrupt("simulated Ctrl-C mid-write")
        return self._inner.execute(sql, *args, **kwargs)

    def commit(self) -> None:
        self._inner.commit()

    def rollback(self) -> None:
        self._inner.rollback()

    @property
    def in_transaction(self) -> bool:
        return self._inner.in_transaction


def test_backfill_rolls_back_on_keyboard_interrupt(tmp_path: Path) -> None:
    svc = _fresh_service(tmp_path)
    ids = [_seed_preference(svc, f"s{i}", str(i)) for i in range(3)]
    _null_fingerprints(svc, *ids)
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    real_conn = get_connection(db_path)
    flaky = _FlakyConnection(real_conn)

    with pytest.raises(KeyboardInterrupt):
        _run_backfill(flaky, force=False)

    assert real_conn.in_transaction is False, "writer lock must be released on interrupt"
    real_conn.close()

    conn = get_connection(db_path)
    try:
        for i in ids:
            assert _fingerprint_of(conn, i) is None, "no writes must have been committed"
    finally:
        conn.close()


def test_backfill_legacy_unknown_keys_payload_is_coerced_deterministically(
    tmp_path: Path,
) -> None:
    """§10.4 L1171: a pre-6g preference row with unknown JSON keys backfills via schema coercion.

    The backfill strips unknown keys through ``coerce_prior_payload_to_schema``,
    so the fingerprint must match what would be computed over the coerced
    (empty-content) tuple — NOT over the raw legacy payload.
    """
    from minx_mcp.core.fingerprint import content_fingerprint

    svc = _fresh_service(tmp_path)
    # Simulate a pre-6g row with only unknown/legacy keys — a realistic shape
    # for rows that predated the current Pydantic model surface.
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, status, confidence,
            payload_json, source, reason, created_at, updated_at
        ) VALUES (
            'preference', 'core', 'legacyrow',
            'active', 0.9,
            '{"legacy_key_1": "whatever", "legacy_key_2": 7}',
            'legacy', '', datetime('now'), datetime('now')
        )
        """
    )
    svc.conn.commit()
    row_id = int(
        svc.conn.execute(
            "SELECT id FROM memories WHERE subject = 'legacyrow'"
        ).fetchone()["id"]
    )
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    rc = main([str(db_path)])
    assert rc == 0

    conn = get_connection(db_path)
    try:
        fp = _fingerprint_of(conn, row_id)
    finally:
        conn.close()

    # With all legacy keys filtered out by coerce_prior_payload_to_schema, a
    # preference's value/note are absent → value_part == "" → we get the
    # degraded 5-tuple fingerprint. This is the *correct* outcome: content is
    # empty after coercion, so dedup treats it as empty content.
    expected = content_fingerprint("preference", "core", "legacyrow", "", "")
    assert fp == expected


def test_backfill_fully_corrupted_payload_json_falls_back_gracefully(
    tmp_path: Path,
) -> None:
    """§10.4 L1171: a row whose payload_json is not valid JSON backfills to the degraded 5-tuple fingerprint."""
    from minx_mcp.core.fingerprint import content_fingerprint

    svc = _fresh_service(tmp_path)
    # Seed a row whose payload_json is outright garbage. This is the exact
    # shape _compute_fingerprint_for_row must handle without raising — spec
    # §5.2 requires graceful degradation to ('type', 'scope', 'subject', '', '').
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, status, confidence,
            payload_json, source, reason, created_at, updated_at
        ) VALUES (
            'preference', 'core', 'garbagerow',
            'active', 0.9,
            'this is not json {]',
            'legacy', '', datetime('now'), datetime('now')
        )
        """
    )
    svc.conn.commit()
    row_id = int(
        svc.conn.execute(
            "SELECT id FROM memories WHERE subject = 'garbagerow'"
        ).fetchone()["id"]
    )
    db_path = Path(svc.conn.execute("PRAGMA database_list").fetchone()["file"])
    svc.close()

    rc = main([str(db_path)])
    assert rc == 0

    conn = get_connection(db_path)
    try:
        fp = _fingerprint_of(conn, row_id)
    finally:
        conn.close()

    expected = content_fingerprint("preference", "core", "garbagerow", "", "")
    assert fp == expected, (
        "fully-corrupted payload_json must degrade to the 5-tuple fingerprint "
        "rather than crashing the backfill"
    )
