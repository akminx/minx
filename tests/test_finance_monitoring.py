from minx_mcp.finance.service import FinanceService


def test_finance_monitoring_reports_category_rollups_and_recurring_income(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    dcu_id = service.conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'DCU'"
    ).fetchone()["id"]
    groceries_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Groceries'"
    ).fetchone()["id"]
    income_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Income'"
    ).fetchone()["id"]
    service.conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, ?, 'csv', 'seed.csv', 'fp')
        """,
        (dcu_id,),
    )
    service.conn.executemany(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, 1, ?, ?, ?, ?, ?, 'manual')
        """,
        [
            (dcu_id, "2026-03-01", "Groceries trip", "H-E-B", -4500, groceries_id),
            (dcu_id, "2026-03-05", "Groceries refill", "H-E-B", -5500, groceries_id),
            (dcu_id, "2026-03-15", "Paycheck", "Employer", 250000, income_id),
        ],
    )
    service.conn.commit()

    result = service.finance_monitoring(period_start="2026-03-01", period_end="2026-03-31")

    assert result["top_categories"][0]["category_name"] == "Groceries"
    assert result["income_patterns"][0]["merchant"] == "Employer"


def test_finance_monitoring_includes_categories_that_only_exist_in_prior_period(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path)
    dcu_id = service.conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'DCU'"
    ).fetchone()["id"]
    groceries_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Groceries'"
    ).fetchone()["id"]
    dining_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Dining Out'"
    ).fetchone()["id"]
    service.conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, ?, 'csv', 'seed.csv', 'fp')
        """,
        (dcu_id,),
    )
    service.conn.executemany(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, 1, ?, ?, ?, ?, ?, 'manual')
        """,
        [
            (dcu_id, "2026-02-20", "Dinner", "Cafe", -3000, dining_id),
            (dcu_id, "2026-03-05", "Groceries refill", "H-E-B", -5500, groceries_id),
        ],
    )
    service.conn.commit()

    result = service.finance_monitoring(period_start="2026-03-01", period_end="2026-03-31")

    changes = {row["category_name"]: row for row in result["changes_vs_prior_period"]}
    assert changes["Dining Out"]["current_total_spent"] == 0.0
    assert changes["Dining Out"]["prior_total_spent"] == 30.0
    assert changes["Dining Out"]["delta_spent"] == -30.0
