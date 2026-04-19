import shutil
import sqlite3
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from minx_mcp import db as db_module
from minx_mcp.db import add_column_if_missing, get_connection, migration_dir


def test_migration_dir_points_at_packaged_minx_mcp_schema_migrations() -> None:
    """Runtime migrations always load from the package tree next to db.py (wheel or source)."""
    expected = Path(db_module.__file__).resolve().parent / "schema" / "migrations"
    assert migration_dir() == expected
    assert expected.is_dir()
    sql_files = sorted(path.name for path in expected.glob("*.sql"))
    assert sql_files, "packaged migrations directory must contain .sql files"


def test_database_bootstrap_creates_platform_and_finance_tables(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    }
    assert "_migrations" in names
    assert "jobs" in names
    assert "job_events" in names
    assert "preferences" in names
    assert "audit_log" in names
    assert "finance_accounts" in names
    assert "finance_categories" in names
    assert "finance_category_rules" in names
    assert "finance_import_batches" in names
    assert "finance_transactions" in names
    assert "finance_transaction_dedupe" in names
    assert "finance_report_runs" in names
    assert "events" in names
    assert "insights" in names
    assert "goals" in names
    assert "v_finance_monthly_spend" not in names


def test_database_bootstrap_creates_meals_nutrition_tables(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    names = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "meals_nutrition_profiles" in names
    assert "meals_nutrition_targets" in names


def test_database_bootstrap_creates_memory_tables(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    names = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "memories" in names
    assert "memory_events" in names
    assert "vault_index" in names
    assert "playbook_runs" in names


def test_memory_events_accept_vault_synced_after_slice6c_migration(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status, payload_json, source, reason
        ) VALUES ('preference', 'core', 'timezone', 0.9, 'active', '{}', 'test', '')
        """
    )
    memory_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        """
        INSERT INTO memory_events (memory_id, event_type, payload_json, actor)
        VALUES (?, 'vault_synced', '{"change": "update"}', 'vault_sync')
        """,
        (memory_id,),
    )
    conn.commit()
    row = conn.execute(
        "SELECT event_type, actor FROM memory_events WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    assert dict(row) == {"event_type": "vault_synced", "actor": "vault_sync"}


def test_memory_events_cascade_when_parent_memory_deleted(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status, payload_json, source, reason
        ) VALUES ('t', 's', 'subj', 0.9, 'active', '{}', 'test', '')
        """
    )
    mid = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.execute(
        """
        INSERT INTO memory_events (memory_id, event_type, payload_json, actor)
        VALUES (?, 'created', '{}', 'system')
        """,
        (mid,),
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS c FROM memory_events WHERE memory_id = ?", (mid,)).fetchone()["c"] == 1
    conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
    conn.commit()
    assert (
        conn.execute("SELECT COUNT(*) AS c FROM memory_events WHERE memory_id = ?", (mid,)).fetchone()["c"] == 0
    )


def test_database_bootstrap_creates_training_tables(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    names = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "training_exercises" in names
    assert "training_programs" in names
    assert "training_program_days" in names
    assert "training_program_exercises" in names
    assert "training_sessions" in names
    assert "training_session_sets" in names
    assert "training_milestones" in names


def test_database_bootstrap_creates_core_indexes(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    indexes = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }

    assert "idx_events_domain_type" in indexes
    assert "idx_events_occurred" in indexes
    assert "idx_insights_dedup" in indexes
    assert "idx_goals_status_domain" in indexes
    assert "idx_goals_period_status" in indexes
    assert "idx_events_occurred_sensitivity" in indexes


def test_database_bootstrap_creates_finance_report_lifecycle_columns(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    columns = {
        row["name"]: row
        for row in conn.execute("PRAGMA table_info(finance_report_runs)").fetchall()
    }
    indexes = {
        row["name"] for row in conn.execute("PRAGMA index_list(finance_report_runs)").fetchall()
    }

    assert "status" in columns
    assert "updated_at" in columns
    assert "error_message" in columns
    assert "idx_finance_report_runs_identity" in indexes


def test_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "minx.db"
    first = get_connection(db_path)
    first.close()
    second = get_connection(db_path)
    count = second.execute("SELECT COUNT(*) AS c FROM _migrations").fetchone()["c"]
    assert count == len(list(migration_dir().glob("*.sql")))


def test_finance_seed_rows_exist(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    accounts = {
        row["name"] for row in conn.execute("SELECT name FROM finance_accounts ORDER BY name")
    }
    categories = {
        row["name"] for row in conn.execute("SELECT name FROM finance_categories ORDER BY name")
    }
    assert {"DCU", "Discover", "Robinhood Gold"} <= accounts
    assert {"Groceries", "Dining Out", "Income", "Uncategorized"} <= categories


def test_connection_enables_required_pragmas(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert foreign_keys == 1
    assert journal_mode == "wal"


def test_apply_migrations_handles_plain_sqlite_connections(tmp_path):
    db_path = tmp_path / "plain.db"
    conn = sqlite3.connect(str(db_path))
    original_row_factory = conn.row_factory

    db_module.apply_migrations(conn)
    db_module.apply_migrations(conn)

    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count == len(list(migration_dir().glob("*.sql")))
    assert conn.row_factory is original_row_factory


def test_add_column_if_missing_is_idempotent_for_repeated_calls(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "idempotent.db"))
    conn.execute("CREATE TABLE sample_table (id INTEGER PRIMARY KEY)")

    first = add_column_if_missing(
        conn,
        table_name="sample_table",
        column_name="notes",
        column_sql="TEXT",
    )
    second = add_column_if_missing(
        conn,
        table_name="sample_table",
        column_name="notes",
        column_sql="TEXT",
    )
    columns = [row[1] for row in conn.execute("PRAGMA table_info(sample_table)").fetchall()]

    assert first is True
    assert second is False
    assert columns.count("notes") == 1


def test_add_column_if_missing_rejects_invalid_identifiers(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "invalid_identifier.db"))
    conn.execute("CREATE TABLE sample_table (id INTEGER PRIMARY KEY)")

    with pytest.raises(ValueError, match="Invalid SQLite identifier"):
        add_column_if_missing(
            conn,
            table_name="sample-table",
            column_name="notes",
            column_sql="TEXT",
        )


def test_add_column_rejects_disallowed_type_fragment(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "bad_fragment.db"))
    conn.execute("CREATE TABLE sample_table (id INTEGER PRIMARY KEY)")

    with pytest.raises(ValueError, match="column_sql"):
        add_column_if_missing(
            conn,
            table_name="sample_table",
            column_name="evil",
            column_sql="TEXT; DROP TABLE sample_table;--",
        )


def test_add_column_if_missing_accepts_integer_not_null_default(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "typed_column.db"))
    conn.execute("CREATE TABLE sample_table (id INTEGER PRIMARY KEY)")

    assert (
        add_column_if_missing(
            conn,
            table_name="sample_table",
            column_name="score",
            column_sql="INTEGER NOT NULL DEFAULT 0",
        )
        is True
    )


def test_add_column_if_missing_raises_for_unknown_table(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "missing_table.db"))

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        add_column_if_missing(
            conn,
            table_name="missing_table",
            column_name="notes",
            column_sql="TEXT",
        )


def test_report_lifecycle_migration_dedupes_existing_report_runs(tmp_path):
    migrations = migration_dir()
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript((migrations / "001_platform.sql").read_text())
    conn.executescript((migrations / "002_finance.sql").read_text())
    conn.executescript((migrations / "003_finance_views.sql").read_text())
    conn.executescript((migrations / "004_finance_amount_cents.sql").read_text())
    conn.executescript((migrations / "005_core.sql").read_text())
    conn.execute(
        """
        CREATE TABLE _migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.executemany(
        "INSERT INTO _migrations (name) VALUES (?)",
        [
            ("001_platform.sql",),
            ("002_finance.sql",),
            ("003_finance_views.sql",),
            ("004_finance_amount_cents.sql",),
            ("005_core.sql",),
        ],
    )
    conn.executemany(
        """
        INSERT INTO finance_report_runs (report_kind, period_start, period_end, vault_path, summary_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("weekly", "2026-03-02", "2026-03-08", "Finance/weekly-old.md", '{"old": true}'),
            ("weekly", "2026-03-02", "2026-03-08", "Finance/weekly-new.md", '{"new": true}'),
        ],
    )
    conn.commit()
    conn.close()

    migrated = get_connection(db_path)
    rows = migrated.execute(
        """
        SELECT report_kind, period_start, period_end, vault_path, status, error_message
        FROM finance_report_runs
        ORDER BY id
        """
    ).fetchall()

    assert len(rows) == 1
    assert rows[0]["vault_path"] == "Finance/weekly-new.md"
    assert rows[0]["status"] == "completed"
    assert rows[0]["error_message"] is None


def test_amount_cents_migration_backfills_existing_rows(tmp_path):
    migrations = migration_dir()
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript((migrations / "001_platform.sql").read_text())
    conn.executescript((migrations / "002_finance.sql").read_text())
    conn.executescript((migrations / "003_finance_views.sql").read_text())
    conn.execute(
        """
        CREATE TABLE _migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.executemany(
        "INSERT INTO _migrations (name) VALUES (?)",
        [("001_platform.sql",), ("002_finance.sql",), ("003_finance_views.sql",)],
    )
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'legacy.csv', 'fp')
        """
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount, category_id, category_source
        ) VALUES (1, 1, '2026-04-01', 'Legacy Amount', 'Store', -12.345, 1, 'uncategorized')
        """
    )
    conn.commit()
    conn.close()

    migrated = get_connection(db_path)
    row = migrated.execute(
        "SELECT amount_cents FROM finance_transactions WHERE description = 'Legacy Amount'"
    ).fetchone()
    assert row["amount_cents"] == -1235

    views = {
        row[0] for row in migrated.execute("SELECT name FROM sqlite_master WHERE type = 'view'")
    }
    assert "v_finance_monthly_spend" not in views


def test_failed_migration_rolls_back_partial_changes(tmp_path, monkeypatch):
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    (migration_root / "001_good.sql").write_text(
        "CREATE TABLE seeded_table (id INTEGER PRIMARY KEY);"
    )
    (migration_root / "002_bad.sql").write_text(
        "CREATE TABLE half_done (id INTEGER PRIMARY KEY);\nTHIS IS NOT VALID SQL;"
    )

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "broken.db"))

    with pytest.raises(sqlite3.DatabaseError):
        db_module.apply_migrations(conn)

    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert "half_done" not in names
    assert "seeded_table" not in names
    assert "_migrations" not in names


def test_concurrent_bootstrap_succeeds_for_same_db_file(tmp_path):
    db_path = tmp_path / "shared.db"
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def bootstrap() -> None:
        try:
            barrier.wait()
            conn = get_connection(db_path)
            conn.close()
        except Exception as exc:  # pragma: no cover - failure path asserted below
            errors.append(exc)

    threads = [threading.Thread(target=bootstrap) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []


def test_built_wheel_includes_packaged_resources(tmp_path):
    project_root = Path(__file__).resolve().parent.parent
    source_root = tmp_path / "source"
    wheel_dir = tmp_path / "wheelhouse"
    shutil.copytree(
        project_root,
        source_root,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".pytest_cache",
            "build",
            "*.egg-info",
            "__pycache__",
        ),
    )
    wheel_dir.mkdir()

    subprocess.run(  # noqa: S603 - test builds wheel using sys.executable and inline setuptools hook
        [
            sys.executable,
            "-c",
            "import sys; from setuptools.build_meta import build_wheel; build_wheel(sys.argv[1])",
            str(wheel_dir),
        ],
        check=True,
        cwd=source_root,
        capture_output=True,
        text=True,
    )

    wheel_path = next(wheel_dir.glob("minx_mcp-*.whl"))
    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())

    assert "minx_mcp/schema/migrations/001_platform.sql" in names
    assert "minx_mcp/schema/migrations/002_finance.sql" in names
    assert "minx_mcp/schema/migrations/003_finance_views.sql" in names
    assert "minx_mcp/schema/migrations/004_finance_amount_cents.sql" in names
    assert "minx_mcp/schema/migrations/005_core.sql" in names
    assert "minx_mcp/schema/migrations/006_finance_report_lifecycle.sql" in names
    assert "minx_mcp/schema/migrations/007_core_goals.sql" in names
    assert "minx_mcp/schema/migrations/008_finance_phase2.sql" in names
    assert "minx_mcp/schema/migrations/009_cleanup.sql" in names
    assert "minx_mcp/schema/migrations/010_meals.sql" in names
    assert "minx_mcp/schema/migrations/011_meals_nutrition.sql" in names
    assert "minx_mcp/schema/migrations/012_training.sql" in names
    assert "minx_mcp/schema/migrations/013_slice6_memory.sql" in names
    assert "minx_mcp/schema/migrations/014_slice6_snapshot_archives.sql" in names
    assert "minx_mcp/schema/migrations/015_slice6_memories_unique_live.sql" in names
    assert "minx_mcp/schema/migrations/016_memory_ttl_and_event_check.sql" in names
    assert "minx_mcp/schema/migrations/017_recipes_vault_synced_at.sql" in names
    assert "minx_mcp/schema/migrations/018_vault_index.sql" in names
    assert "minx_mcp/schema/migrations/019_playbook_runs.sql" in names

    # Finance markdown templates ship alongside the package so installed wheels
    # can render weekly/monthly reports without relying on the repo layout.
    assert "minx_mcp/finance/templates/finance-weekly-summary.md" in names
    assert "minx_mcp/finance/templates/finance-monthly-summary.md" in names

    # Meals ships the recipe starter scaffold as package data so installed wheels
    # (and the minx-meals ``recipe_template`` tool) can serve it without relying
    # on the repo layout.
    assert "minx_mcp/meals/templates/recipe-starter.md" in names

    assert "minx_mcp/core/templates/wiki/entity.md" in names
    assert "minx_mcp/core/templates/wiki/pattern.md" in names
    assert "minx_mcp/core/templates/wiki/review.md" in names
    assert "minx_mcp/core/templates/wiki/goal.md" in names


# Issue 8c regression: migration 018 applied to a pre-6c DB preserves existing rows
# and the new vault_synced event type becomes valid.
def test_migration_018_applied_to_pre_6c_db_preserves_memory_events_and_adds_vault_index(tmp_path):
    """Apply migrations up through 017 manually, seed memory_events rows, then let
    apply_migrations run 018 and verify rows are preserved and vault_synced is accepted."""
    migrations = migration_dir()
    db_path = tmp_path / "pre6c.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    pre_6c_migrations = [
        "001_platform.sql",
        "002_finance.sql",
        "003_finance_views.sql",
        "004_finance_amount_cents.sql",
        "005_core.sql",
        "006_finance_report_lifecycle.sql",
        "007_core_goals.sql",
        "008_finance_phase2.sql",
        "009_cleanup.sql",
        "010_meals.sql",
        "011_meals_nutrition.sql",
        "012_training.sql",
        "013_slice6_memory.sql",
        "014_slice6_snapshot_archives.sql",
        "015_slice6_memories_unique_live.sql",
        "016_memory_ttl_and_event_check.sql",
        "017_recipes_vault_synced_at.sql",
    ]
    for name in pre_6c_migrations:
        conn.executescript((migrations / name).read_text())

    # Create the _migrations table and record the pre-6c migrations.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now')),
            checksum TEXT
        )
        """
    )
    for name in pre_6c_migrations:
        import hashlib
        content = (migrations / name).read_text(encoding="utf-8")
        checksum = hashlib.sha256(content.encode()).hexdigest()
        conn.execute(
            "INSERT INTO _migrations (name, checksum) VALUES (?, ?)",
            (name, checksum),
        )

    # Seed a memory and two pre-existing memory_events rows.
    conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status, payload_json, source, reason
        ) VALUES ('preference', 'core', 'legacy_pref', 0.9, 'active', '{}', 'test', '')
        """
    )
    mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO memory_events (memory_id, event_type, payload_json, actor) VALUES (?, 'created', '{}', 'system')",
        (mid,),
    )
    conn.execute(
        "INSERT INTO memory_events (memory_id, event_type, payload_json, actor)"
        " VALUES (?, 'promoted', '{}', 'detector')",
        (mid,),
    )
    conn.commit()
    conn.close()

    # Now apply 018 (and later) via get_connection which calls apply_migrations.
    migrated = get_connection(db_path)

    # All pre-existing rows must be preserved.
    event_rows = migrated.execute(
        "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id", (mid,)
    ).fetchall()
    assert len(event_rows) == 2
    assert event_rows[0]["event_type"] == "created"
    assert event_rows[1]["event_type"] == "promoted"

    # vault_index table must exist.
    tables = {
        row["name"]
        for row in migrated.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "vault_index" in tables

    # vault_synced is now a valid event_type after 018.
    migrated.execute(
        "INSERT INTO memory_events (memory_id, event_type, payload_json, actor)"
        " VALUES (?, 'vault_synced', '{}', 'vault_sync')",
        (mid,),
    )
    migrated.commit()
    row = migrated.execute(
        "SELECT event_type FROM memory_events WHERE memory_id = ? AND event_type = 'vault_synced'",
        (mid,),
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "vault_synced"


# Issue 9 regression: wiki template __init__.py files exist so they are recognized as packages.
def test_wiki_template_packages_have_init_py() -> None:
    import minx_mcp.core.templates
    import minx_mcp.core.templates.wiki

    assert hasattr(minx_mcp.core.templates, "__file__")
    assert hasattr(minx_mcp.core.templates.wiki, "__file__")


def test_missing_migrations_preserve_row_factory(tmp_path, monkeypatch):
    missing_root = tmp_path / "missing"
    conn = sqlite3.connect(str(tmp_path / "plain.db"))
    original_row_factory = conn.row_factory

    monkeypatch.setattr(db_module, "migration_dir", lambda: missing_root)

    with pytest.raises(FileNotFoundError):
        db_module.apply_migrations(conn)

    assert conn.row_factory is original_row_factory
    assert not conn.in_transaction


def test_partial_migration_set_fails_closed(tmp_path, monkeypatch):
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    (migration_root / "001_platform.sql").write_text(
        "CREATE TABLE one_table (id INTEGER PRIMARY KEY);"
    )
    (migration_root / "003_finance_views.sql").write_text(
        "CREATE TABLE three_table (id INTEGER PRIMARY KEY);"
    )
    conn = sqlite3.connect(str(tmp_path / "gap.db"))

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)

    with pytest.raises(FileNotFoundError):
        db_module.apply_migrations(conn)

    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "one_table" not in names
    assert "three_table" not in names
    assert "_migrations" not in names


def test_unreadable_migration_rolls_back_and_restores_connection(tmp_path, monkeypatch):
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    target = migration_root / "001_platform.sql"
    target.write_text("CREATE TABLE should_not_exist (id INTEGER PRIMARY KEY);")
    conn = sqlite3.connect(str(tmp_path / "broken.db"))
    original_row_factory = conn.row_factory
    original_read_text = Path.read_text

    def broken_read_text(self: Path, *args, **kwargs):
        if self == target:
            raise OSError("cannot read migration")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    monkeypatch.setattr(Path, "read_text", broken_read_text)

    with pytest.raises(OSError):
        db_module.apply_migrations(conn)

    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "should_not_exist" not in names
    assert "_migrations" not in names
    assert conn.row_factory is original_row_factory
    assert not conn.in_transaction
