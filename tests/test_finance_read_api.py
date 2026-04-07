from __future__ import annotations

from minx_mcp.db import get_connection
from minx_mcp.jobs import STUCK_JOB_TIMEOUT_MINUTES


def test_get_spending_summary_returns_total_categories_and_top_merchants(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-01",
        description="Neighborhood Market",
        merchant="Neighborhood Market",
        amount_cents=-4525,
        category_id=2,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-02",
        description="Coffee Shop",
        merchant="Coffee Shop",
        amount_cents=-1250,
        category_id=3,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-03",
        description="Neighborhood Market",
        merchant="Neighborhood Market",
        amount_cents=-1875,
        category_id=2,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-04",
        description="Payroll",
        merchant="Employer",
        amount_cents=250000,
        category_id=4,
    )
    conn.commit()

    from minx_mcp.finance.read_api import FinanceReadAPI

    summary = FinanceReadAPI(conn).get_spending_summary("2026-03-01", "2026-03-07")

    assert summary.total_spent_cents == 7650
    assert [(item.category_name, item.total_spent_cents) for item in summary.by_category] == [
        ("Groceries", 6400),
        ("Dining Out", 1250),
    ]
    assert [(item.merchant, item.total_spent_cents, item.transaction_count) for item in summary.top_merchants] == [
        ("Neighborhood Market", 6400, 2),
        ("Coffee Shop", 1250, 1),
    ]


def test_get_uncategorized_returns_count_and_total_for_date_range(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-01",
        description="Mystery Charge",
        merchant="Unknown",
        amount_cents=-3400,
        category_id=1,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-03",
        description="Another Mystery Charge",
        merchant="Unknown",
        amount_cents=-125,
        category_id=1,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-05",
        description="Groceries",
        merchant="HEB",
        amount_cents=-2000,
        category_id=2,
    )
    conn.commit()

    from minx_mcp.finance.read_api import FinanceReadAPI

    summary = FinanceReadAPI(conn).get_uncategorized("2026-03-01", "2026-03-04")

    assert summary.transaction_count == 2
    assert summary.total_spent_cents == 3525


def test_get_import_job_issues_returns_failed_and_stale_jobs_in_deterministic_order(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    conn.execute(
        """
        INSERT INTO jobs (id, job_type, status, requested_by, source_ref, error_message, updated_at)
        VALUES
            ('job-failed-a', 'finance_import', 'failed', 'test', '/imports/a.csv', 'bad csv', '2026-03-01 10:00:00'),
            ('job-failed-b', 'finance_import', 'failed', 'test', '/imports/b.csv', 'timeout', '2026-03-01 10:00:00'),
            ('job-stale', 'finance_import', 'running', 'test', '/imports/c.csv', NULL, datetime('now', ?)),
            ('job-healthy', 'finance_import', 'running', 'test', '/imports/d.csv', NULL, datetime('now'))
        """,
        (f"-{STUCK_JOB_TIMEOUT_MINUTES + 1} minutes",),
    )
    conn.commit()

    from minx_mcp.finance.read_api import FinanceReadAPI

    issues = FinanceReadAPI(conn).get_import_job_issues()

    assert [(issue.job_id, issue.issue_kind, issue.status, issue.source_ref) for issue in issues] == [
        ("job-failed-a", "failed", "failed", "/imports/a.csv"),
        ("job-failed-b", "failed", "failed", "/imports/b.csv"),
        ("job-stale", "stale", "running", "/imports/c.csv"),
    ]


def test_get_import_job_issues_uses_jobs_table_and_stuck_timeout_policy(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    conn.execute(
        """
        INSERT INTO finance_import_batches (account_id, source_type, source_ref, raw_fingerprint, imported_at)
        VALUES (1, 'csv', 'old-batch.csv', 'stale-batch', datetime('now', '-7 days'))
        """
    )
    conn.execute(
        """
        INSERT INTO jobs (id, job_type, status, requested_by, source_ref, updated_at)
        VALUES
            ('job-just-inside', 'finance_import', 'running', 'test', '/imports/fresh.csv', datetime('now', ?)),
            ('job-just-outside', 'finance_import', 'running', 'test', '/imports/stale.csv', datetime('now', ?))
        """,
        (
            f"-{STUCK_JOB_TIMEOUT_MINUTES - 1} minutes",
            f"-{STUCK_JOB_TIMEOUT_MINUTES + 1} minutes",
        ),
    )
    conn.commit()

    from minx_mcp.finance.read_api import FinanceReadAPI

    issues = FinanceReadAPI(conn).get_import_job_issues()

    assert [issue.job_id for issue in issues] == ["job-just-outside"]
    assert issues[0].issue_kind == "stale"


def test_get_period_comparison_returns_totals_and_category_deltas(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-10",
        description="Groceries Current",
        merchant="Market",
        amount_cents=-5000,
        category_id=2,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-11",
        description="Dining Current",
        merchant="Cafe",
        amount_cents=-2000,
        category_id=3,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-03",
        description="Groceries Prior",
        merchant="Market",
        amount_cents=-3500,
        category_id=2,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-04",
        description="Subscriptions Prior",
        merchant="Streamer",
        amount_cents=-1000,
        category_id=5,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-05",
        description="Shopping Prior",
        merchant="Big Box",
        amount_cents=-9000,
        category_id=6,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-12",
        description="Shopping Current",
        merchant="Big Box",
        amount_cents=-1000,
        category_id=6,
    )
    conn.commit()

    from minx_mcp.finance.read_api import FinanceReadAPI

    comparison = FinanceReadAPI(conn).get_period_comparison(
        "2026-03-10",
        "2026-03-16",
        "2026-03-03",
        "2026-03-09",
    )

    assert comparison.current_total_spent_cents == 8000
    assert comparison.prior_total_spent_cents == 13500
    assert [
        (
            item.category_name,
            item.current_total_spent_cents,
            item.prior_total_spent_cents,
            item.delta_spent_cents,
        )
        for item in comparison.category_deltas
    ] == [
        ("Shopping", 1000, 9000, -8000),
        ("Dining Out", 2000, 0, 2000),
        ("Groceries", 5000, 3500, 1500),
        ("Subscriptions", 0, 1000, -1000),
    ]


def test_finance_read_api_aggregations_use_amount_cents_not_legacy_amount(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    conn.execute("ALTER TABLE finance_transactions ADD COLUMN amount REAL")
    _seed_batch(conn)
    _insert_transaction(
        conn,
        posted_at="2026-03-01",
        description="Groceries",
        merchant="HEB",
        amount_cents=-1099,
        category_id=2,
        amount=-9999.99,
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-02",
        description="Uncategorized",
        merchant="Mystery",
        amount_cents=-501,
        category_id=1,
        amount=1234.56,
    )
    _insert_transaction(
        conn,
        posted_at="2026-02-22",
        description="Prior Groceries",
        merchant="HEB",
        amount_cents=-701,
        category_id=2,
        amount=-0.01,
    )
    conn.commit()

    from minx_mcp.finance.read_api import FinanceReadAPI

    api = FinanceReadAPI(conn)
    spending = api.get_spending_summary("2026-03-01", "2026-03-07")
    uncategorized = api.get_uncategorized("2026-03-01", "2026-03-07")
    comparison = api.get_period_comparison(
        "2026-03-01",
        "2026-03-07",
        "2026-02-22",
        "2026-02-28",
    )

    assert spending.total_spent_cents == 1600
    assert uncategorized.total_spent_cents == 501
    assert comparison.current_total_spent_cents == 1600
    assert comparison.prior_total_spent_cents == 701


def _seed_batch(conn) -> None:
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, 1, 'csv', 'seed.csv', 'seed-fingerprint')
        """
    )


def _insert_transaction(
    conn,
    *,
    posted_at: str,
    description: str,
    merchant: str,
    amount_cents: int,
    category_id: int,
    amount: float | None = None,
) -> None:
    columns = [
        "account_id",
        "batch_id",
        "posted_at",
        "description",
        "merchant",
        "amount_cents",
        "category_id",
        "category_source",
    ]
    values = [1, 1, posted_at, description, merchant, amount_cents, category_id, "manual"]

    if amount is not None:
        columns.append("amount")
        values.append(amount)

    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"""
        INSERT INTO finance_transactions ({", ".join(columns)})
        VALUES ({placeholders})
        """,
        values,
    )
