from minx_mcp.db import get_connection


def test_database_bootstrap_creates_platform_and_finance_tables(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
    }
    assert "_migrations" in names
    assert "jobs" in names
    assert "preferences" in names
    assert "audit_log" in names
    assert "finance_accounts" in names
    assert "finance_categories" in names
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
