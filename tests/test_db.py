import sqlite3

import pytest

from minx_mcp import db as db_module
from minx_mcp.db import get_connection


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
    assert "v_finance_monthly_spend" in names


def test_migrations_are_idempotent(tmp_path):
    db_path = tmp_path / "minx.db"
    first = get_connection(db_path)
    first.close()
    second = get_connection(db_path)
    count = second.execute("SELECT COUNT(*) AS c FROM _migrations").fetchone()["c"]
    assert count == 3


def test_finance_seed_rows_exist(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    accounts = {
        row["name"]
        for row in conn.execute("SELECT name FROM finance_accounts ORDER BY name")
    }
    categories = {
        row["name"]
        for row in conn.execute("SELECT name FROM finance_categories ORDER BY name")
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

    db_module.apply_migrations(conn)
    db_module.apply_migrations(conn)

    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    assert count == 3


def test_failed_migration_rolls_back_partial_changes(tmp_path, monkeypatch):
    migration_root = tmp_path / "migrations"
    migration_root.mkdir()
    (migration_root / "001_good.sql").write_text(
        "CREATE TABLE seeded_table (id INTEGER PRIMARY KEY);"
    )
    (migration_root / "002_bad.sql").write_text(
        "CREATE TABLE half_done (id INTEGER PRIMARY KEY);\n"
        "THIS IS NOT VALID SQL;"
    )

    monkeypatch.setattr(db_module, "migration_dir", lambda: migration_root)
    conn = sqlite3.connect(str(tmp_path / "broken.db"))

    with pytest.raises(sqlite3.DatabaseError):
        db_module.apply_migrations(conn)

    names = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    applied = {
        row[0]
        for row in conn.execute("SELECT name FROM _migrations").fetchall()
    }

    assert "seeded_table" in names
    assert "half_done" not in names
    assert applied == {"001_good.sql"}
