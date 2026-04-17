"""Tests for SHA-256 checksum verification in the migration system."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from minx_mcp import db as db_module
from minx_mcp.db import apply_migrations, migration_dir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _checksums_in_db(conn: sqlite3.Connection) -> dict[str, str | None]:
    """Return {name: checksum} for every row in _migrations."""
    rows = conn.execute("SELECT name, checksum FROM _migrations ORDER BY name").fetchall()
    return {row[0]: row[1] for row in rows}


def _file_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_text().encode()).hexdigest()


# ---------------------------------------------------------------------------
# 1. Checksum storage
# ---------------------------------------------------------------------------


def test_checksums_stored_after_migration(tmp_path):
    """Every applied migration must have a non-null checksum that matches the file content."""
    conn = sqlite3.connect(str(tmp_path / "minx.db"))
    apply_migrations(conn)

    stored = _checksums_in_db(conn)
    assert stored, "No rows in _migrations after apply_migrations"

    for path in sorted(migration_dir().glob("*.sql")):
        expected = _file_checksum(path)
        actual = stored.get(path.name)
        assert actual is not None, f"checksum is NULL for {path.name}"
        assert actual == expected, (
            f"Stored checksum for {path.name} does not match file content. "
            f"stored={actual[:12]}…  expected={expected[:12]}…"
        )


def test_all_migrations_recorded(tmp_path):
    """Row count in _migrations equals the number of .sql files in the packaged dir."""
    conn = sqlite3.connect(str(tmp_path / "minx.db"))
    apply_migrations(conn)

    sql_files = sorted(migration_dir().glob("*.sql"))
    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count == len(sql_files)


# ---------------------------------------------------------------------------
# 2. Tampered migration detection
# ---------------------------------------------------------------------------


def test_tampered_migration_raises_runtime_error(tmp_path, monkeypatch):
    """Modifying a migration file after it has been applied must raise RuntimeError."""
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    m1 = migration_root / "001_init.sql"
    m1.write_text("CREATE TABLE things (id INTEGER PRIMARY KEY);")

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    apply_migrations(conn)

    # Tamper with the migration file
    m1.write_text("CREATE TABLE things (id INTEGER PRIMARY KEY, extra TEXT);")

    with pytest.raises(RuntimeError, match="has been modified after application"):
        apply_migrations(conn)


def test_tampered_migration_error_message_names_file(tmp_path, monkeypatch):
    """The RuntimeError message must include the offending migration filename."""
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    m1 = migration_root / "001_things.sql"
    m1.write_text("CREATE TABLE foo (id INTEGER PRIMARY KEY);")

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    apply_migrations(conn)

    m1.write_text("CREATE TABLE foo (id INTEGER PRIMARY KEY, col TEXT);")

    with pytest.raises(RuntimeError, match="001_things.sql"):
        apply_migrations(conn)


def test_tampered_second_migration_detected(tmp_path, monkeypatch):
    """Tampering with any migration (not just the first) must be caught."""
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    m1 = migration_root / "001_first.sql"
    m2 = migration_root / "002_second.sql"
    m1.write_text("CREATE TABLE alpha (id INTEGER PRIMARY KEY);")
    m2.write_text("CREATE TABLE beta (id INTEGER PRIMARY KEY);")

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    apply_migrations(conn)

    # Only tamper with the second file
    m2.write_text("CREATE TABLE beta (id INTEGER PRIMARY KEY, extra TEXT);")

    with pytest.raises(RuntimeError, match="has been modified after application"):
        apply_migrations(conn)


def test_tampered_migration_with_real_migrations(tmp_path, monkeypatch):
    """Tampering with one of the real packaged migrations must raise RuntimeError."""
    import shutil

    # Copy packaged migrations to a temp dir so we can modify them safely
    migration_root = tmp_path / "migrations"
    shutil.copytree(str(migration_dir()), str(migration_root))

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    apply_migrations(conn)

    # Append a comment to migration 001 — still valid SQL, but different hash
    target = migration_root / "001_platform.sql"
    target.write_text(target.read_text() + "\n-- tampered\n")

    with pytest.raises(RuntimeError, match="has been modified after application"):
        apply_migrations(conn)


# ---------------------------------------------------------------------------
# 3. Clean re-run (idempotency)
# ---------------------------------------------------------------------------


def test_clean_rerun_succeeds(tmp_path):
    """Applying migrations twice with no file changes must succeed without error."""
    conn = sqlite3.connect(str(tmp_path / "minx.db"))
    apply_migrations(conn)
    apply_migrations(conn)  # must not raise

    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    sql_files = sorted(migration_dir().glob("*.sql"))
    assert count == len(sql_files)


def test_clean_rerun_checksums_unchanged(tmp_path):
    """Checksums stored on first run must be identical after a second run."""
    conn = sqlite3.connect(str(tmp_path / "minx.db"))
    apply_migrations(conn)
    first_run = _checksums_in_db(conn)

    apply_migrations(conn)
    second_run = _checksums_in_db(conn)

    assert first_run == second_run


def test_get_connection_idempotent_with_checksums(tmp_path):
    """get_connection (which calls apply_migrations) is safe to call repeatedly."""
    from minx_mcp.db import get_connection

    db_path = tmp_path / "minx.db"
    c1 = get_connection(db_path)
    c1.close()
    c2 = get_connection(db_path)
    c2.close()
    c3 = get_connection(db_path)

    count = c3.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    sql_files = sorted(migration_dir().glob("*.sql"))
    assert count == len(sql_files)


# ---------------------------------------------------------------------------
# 4. New migration added
# ---------------------------------------------------------------------------


def test_new_migration_applied_after_initial_run(tmp_path, monkeypatch):
    """Adding a new migration file after initial apply must run only that file."""
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    m1 = migration_root / "001_base.sql"
    m1.write_text("CREATE TABLE base_table (id INTEGER PRIMARY KEY);")

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    apply_migrations(conn)

    # Verify initial state
    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count == 1

    # Add new migration
    m2 = migration_root / "002_extra.sql"
    m2.write_text("CREATE TABLE extra_table (id INTEGER PRIMARY KEY);")

    apply_migrations(conn)

    # Both should be recorded now
    stored = _checksums_in_db(conn)
    assert "001_base.sql" in stored
    assert "002_extra.sql" in stored
    assert stored["002_extra.sql"] == _file_checksum(m2)

    # The new table should actually exist
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "extra_table" in names


def test_new_migration_preserves_existing_checksums(tmp_path, monkeypatch):
    """Existing checksums must be unchanged when a new migration is added."""
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    m1 = migration_root / "001_base.sql"
    m1.write_text("CREATE TABLE base_table (id INTEGER PRIMARY KEY);")

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    apply_migrations(conn)
    first_checksums = _checksums_in_db(conn)

    m2 = migration_root / "002_extra.sql"
    m2.write_text("CREATE TABLE extra_table (id INTEGER PRIMARY KEY);")
    apply_migrations(conn)

    second_checksums = _checksums_in_db(conn)
    # Checksum for 001 must not change
    assert second_checksums["001_base.sql"] == first_checksums["001_base.sql"]


def test_new_migration_only_new_table_created(tmp_path, monkeypatch):
    """When a new migration is applied, only its effects appear; old tables remain."""
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    m1 = migration_root / "001_base.sql"
    m1.write_text("CREATE TABLE base_table (id INTEGER PRIMARY KEY);")

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    apply_migrations(conn)

    m2 = migration_root / "002_new.sql"
    m2.write_text("CREATE TABLE new_table (id INTEGER PRIMARY KEY);")
    apply_migrations(conn)

    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "base_table" in names
    assert "new_table" in names


# ---------------------------------------------------------------------------
# 5. 009_cleanup effects
# ---------------------------------------------------------------------------


def test_009_cleanup_view_does_not_exist(tmp_path):
    """After all migrations, v_finance_monthly_spend must not exist."""
    from minx_mcp.db import get_connection

    conn = get_connection(tmp_path / "minx.db")
    views = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
    assert "v_finance_monthly_spend" not in views


def test_009_cleanup_index_exists(tmp_path):
    """After all migrations, idx_events_occurred_sensitivity must exist."""
    from minx_mcp.db import get_connection

    conn = get_connection(tmp_path / "minx.db")
    indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_events_occurred_sensitivity" in indexes


def test_009_cleanup_index_covers_correct_columns(tmp_path):
    """idx_events_occurred_sensitivity must cover (occurred_at, sensitivity)."""
    from minx_mcp.db import get_connection

    conn = get_connection(tmp_path / "minx.db")
    # PRAGMA index_info returns rows with seqno, cid, name for each column
    index_cols = [
        row[2]
        for row in conn.execute("PRAGMA index_info(idx_events_occurred_sensitivity)").fetchall()
    ]
    assert index_cols == ["occurred_at", "sensitivity"]


def test_009_migration_has_stored_checksum(tmp_path):
    """009_cleanup.sql must have a stored checksum after migration."""
    from minx_mcp.db import get_connection

    conn = get_connection(tmp_path / "minx.db")
    row = conn.execute("SELECT checksum FROM _migrations WHERE name = '009_cleanup.sql'").fetchone()
    assert row is not None, "009_cleanup.sql not found in _migrations"
    assert row[0] is not None, "checksum is NULL for 009_cleanup.sql"

    expected = _file_checksum(migration_dir() / "009_cleanup.sql")
    assert row[0] == expected


def test_latest_migration_recorded_in_order(tmp_path):
    """The latest migration file must be the last row when sorted by name."""
    from minx_mcp.db import get_connection

    conn = get_connection(tmp_path / "minx.db")
    names = [
        row[0] for row in conn.execute("SELECT name FROM _migrations ORDER BY name").fetchall()
    ]
    latest = sorted(path.name for path in migration_dir().glob("*.sql"))[-1]
    assert names[-1] == latest
