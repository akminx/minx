from __future__ import annotations

from minx_mcp.db import get_connection
from minx_mcp.finance.analytics import (
    _prior_period_window,
    build_finance_monitoring,
    find_uncategorized,
    sensitive_query_count,
    sensitive_query_total_cents,
)


def _seed_batch(conn) -> None:
    conn.execute(
        "INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint) "
        "VALUES (1, 1, 'csv', 'seed.csv', 'seed')"
    )


def _insert_transaction(
    conn,
    *,
    posted_at: str,
    merchant: str,
    amount_cents: int,
    category_id: int | None = None,
    description: str = "Test",
) -> None:
    conn.execute(
        """
        INSERT INTO finance_transactions
            (account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source)
        VALUES (1, 1, ?, ?, ?, ?, ?, 'manual')
        """,
        (posted_at, description, merchant, amount_cents, category_id),
    )


# ---------------------------------------------------------------------------
# _prior_period_window
# ---------------------------------------------------------------------------


def test_prior_period_window_7_day_span():
    prior_start, prior_end = _prior_period_window("2026-03-08", "2026-03-14")
    assert prior_start == "2026-03-01"
    assert prior_end == "2026-03-07"


def test_prior_period_window_single_day():
    prior_start, prior_end = _prior_period_window("2026-03-15", "2026-03-15")
    assert prior_start == "2026-03-14"
    assert prior_end == "2026-03-14"


def test_prior_period_window_month_boundary_crossing():
    prior_start, prior_end = _prior_period_window("2026-03-01", "2026-03-31")
    assert prior_start == "2026-01-29"
    assert prior_end == "2026-02-28"


# ---------------------------------------------------------------------------
# sensitive_query_total_cents
# ---------------------------------------------------------------------------


def test_sensitive_query_total_cents_returns_zero_for_no_matches(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    conn.commit()

    total = sensitive_query_total_cents(conn, start_date="2026-03-01", end_date="2026-03-31")

    assert total == 0


def test_sensitive_query_total_cents_respects_category_filter(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    dining_id = conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Dining Out'"
    ).fetchone()["id"]
    groceries_id = conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Groceries'"
    ).fetchone()["id"]
    _insert_transaction(
        conn, posted_at="2026-03-10", merchant="Cafe", amount_cents=-1000, category_id=dining_id
    )
    _insert_transaction(
        conn, posted_at="2026-03-10", merchant="HEB", amount_cents=-5000, category_id=groceries_id
    )
    conn.commit()

    total = sensitive_query_total_cents(conn, category_name="Dining Out")

    assert total == 1000


def test_sensitive_query_total_cents_respects_merchant_filter(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(conn, posted_at="2026-03-10", merchant="Cafe", amount_cents=-1200)
    _insert_transaction(conn, posted_at="2026-03-10", merchant="HEB", amount_cents=-5000)
    conn.commit()

    total = sensitive_query_total_cents(conn, merchant="Cafe")

    assert total == 1200


def test_sensitive_query_total_cents_end_date_is_inclusive(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(conn, posted_at="2026-03-15", merchant="Cafe", amount_cents=-800)
    _insert_transaction(conn, posted_at="2026-03-16", merchant="HEB", amount_cents=-600)
    conn.commit()

    total_with = sensitive_query_total_cents(conn, start_date="2026-03-15", end_date="2026-03-15")
    total_without = sensitive_query_total_cents(
        conn, start_date="2026-03-16", end_date="2026-03-16"
    )

    assert total_with == 800
    assert total_without == 600


# ---------------------------------------------------------------------------
# sensitive_query_count
# ---------------------------------------------------------------------------


def test_sensitive_query_count_counts_correctly(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(conn, posted_at="2026-03-10", merchant="Cafe", amount_cents=-1000)
    _insert_transaction(conn, posted_at="2026-03-11", merchant="Cafe", amount_cents=-1000)
    _insert_transaction(conn, posted_at="2026-03-12", merchant="HEB", amount_cents=-5000)
    conn.commit()

    count = sensitive_query_count(conn, merchant="Cafe")

    assert count == 2


def test_sensitive_query_count_respects_date_filter(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(conn, posted_at="2026-03-10", merchant="Cafe", amount_cents=-1000)
    _insert_transaction(conn, posted_at="2026-03-20", merchant="Cafe", amount_cents=-1000)
    conn.commit()

    count = sensitive_query_count(conn, start_date="2026-03-15", end_date="2026-03-31")

    assert count == 1


def test_sensitive_query_count_is_spending_only_and_matches_total(tmp_path):
    """The count must exclude refunds/credits so it stays symmetric with
    :func:`sensitive_query_total_cents`. Previously the count included
    credits while the total excluded them, producing internally
    inconsistent audit answers for the same filters.
    """
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    # Three debits (spending) and one credit (refund) for the same merchant.
    _insert_transaction(conn, posted_at="2026-03-10", merchant="Cafe", amount_cents=-1000)
    _insert_transaction(conn, posted_at="2026-03-11", merchant="Cafe", amount_cents=-1500)
    _insert_transaction(conn, posted_at="2026-03-12", merchant="Cafe", amount_cents=-2000)
    _insert_transaction(conn, posted_at="2026-03-13", merchant="Cafe", amount_cents=+500)
    conn.commit()

    count = sensitive_query_count(conn, merchant="Cafe")
    total = sensitive_query_total_cents(conn, merchant="Cafe")

    # 3 debit rows, and a total of 1000 + 1500 + 2000 = 4500 cents of spending.
    assert count == 3
    assert total == 4500


# ---------------------------------------------------------------------------
# find_uncategorized
# ---------------------------------------------------------------------------


def test_find_uncategorized_returns_only_uncategorized_in_window(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    dining_id = conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Dining Out'"
    ).fetchone()["id"]
    _insert_transaction(
        conn, posted_at="2026-03-10", merchant="Cafe", amount_cents=-1000, description="Cafe meal"
    )
    _insert_transaction(
        conn,
        posted_at="2026-03-11",
        merchant="HEB",
        amount_cents=-5000,
        category_id=dining_id,
        description="HEB shop",
    )
    conn.commit()

    results = find_uncategorized(conn, "2026-03-01", "2026-03-31")

    assert len(results) == 1
    assert results[0]["description"] == "Cafe meal"


def test_find_uncategorized_end_exclusive_semantics(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    _seed_batch(conn)
    _insert_transaction(conn, posted_at="2026-03-15", merchant="Cafe", amount_cents=-1000)
    conn.commit()

    included = find_uncategorized(conn, "2026-03-01", "2026-03-16")
    excluded = find_uncategorized(conn, "2026-03-01", "2026-03-15")

    assert len(included) == 1
    assert len(excluded) == 0


# ---------------------------------------------------------------------------
# build_finance_monitoring
# ---------------------------------------------------------------------------


def test_build_finance_monitoring_empty_db_returns_zeros_without_crash(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    result = build_finance_monitoring(conn, period_start="2026-03-01", period_end="2026-03-31")

    assert result["top_categories"] == []
    assert result["top_merchants"] == []
    assert result["uncategorized_summary"]["transaction_count"] == 0
    assert result["uncategorized_summary"]["total_spent"] == 0.0
