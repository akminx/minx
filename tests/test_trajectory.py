from __future__ import annotations

from minx_mcp.core.goals import GoalService
from minx_mcp.core.models import GoalCreateInput
from minx_mcp.core.trajectory import get_goal_trajectory
from minx_mcp.db import get_connection


def test_get_goal_trajectory_returns_completed_weekly_periods_and_sparse_status_counts(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    goal = GoalService(conn).create_goal(
        GoalCreateInput(
            goal_type="spending_cap",
            title="Dining cap",
            metric_type="sum_below",
            target_value=20000,
            period="weekly",
            domain="finance",
            category_names=["Dining Out"],
            merchant_names=[],
            account_names=[],
            starts_on="2026-03-01",
            ends_on=None,
            notes=None,
        )
    )
    _seed_weekly_spend(conn, "2026-03-17", 19800)
    _seed_weekly_spend(conn, "2026-03-24", 19500)
    _seed_weekly_spend(conn, "2026-03-31", 19000)
    conn.commit()
    conn.close()

    result = get_goal_trajectory(
        db_path,
        goal_id=goal.id,
        periods=3,
        as_of_date="2026-04-10",
    )

    assert [item["actual_value"] for item in result["trajectory"]] == [19800, 19500, 19000]
    assert result["trend"] == "improving"
    assert result["status_counts"] == {"watch": 3}


def test_get_goal_trajectory_samples_rolling_28d_windows_weekly(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    goal = GoalService(conn).create_goal(
        GoalCreateInput(
            goal_type="spending_cap",
            title="Rolling dining cap",
            metric_type="sum_below",
            target_value=50000,
            period="rolling_28d",
            domain="finance",
            category_names=["Dining Out"],
            merchant_names=[],
            account_names=[],
            starts_on="2026-02-01",
            ends_on=None,
            notes=None,
        )
    )
    _seed_single_transaction(conn, "2026-03-10", 1000)
    conn.commit()
    conn.close()

    result = get_goal_trajectory(
        db_path,
        goal_id=goal.id,
        periods=1,
        as_of_date="2026-04-10",
    )

    assert result["trajectory"][0]["period_end"] == "2026-04-05"


def test_get_goal_trajectory_includes_week_completed_on_as_of_date(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    goal = GoalService(conn).create_goal(
        GoalCreateInput(
            goal_type="spending_cap",
            title="Dining cap",
            metric_type="sum_below",
            target_value=20000,
            period="weekly",
            domain="finance",
            category_names=["Dining Out"],
            merchant_names=[],
            account_names=[],
            starts_on="2026-03-01",
            ends_on=None,
            notes=None,
        )
    )
    _seed_single_transaction(conn, "2026-04-08", 18000)
    conn.commit()
    conn.close()

    result = get_goal_trajectory(
        db_path,
        goal_id=goal.id,
        periods=1,
        as_of_date="2026-04-12",
    )

    assert result["trajectory"][0]["period_end"] == "2026-04-12"


def test_get_goal_trajectory_includes_month_completed_on_as_of_date(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    goal = GoalService(conn).create_goal(
        GoalCreateInput(
            goal_type="spending_cap",
            title="Dining cap",
            metric_type="sum_below",
            target_value=50000,
            period="monthly",
            domain="finance",
            category_names=["Dining Out"],
            merchant_names=[],
            account_names=[],
            starts_on="2026-02-01",
            ends_on=None,
            notes=None,
        )
    )
    _seed_single_transaction(conn, "2026-03-15", 18000)
    conn.commit()
    conn.close()

    result = get_goal_trajectory(
        db_path,
        goal_id=goal.id,
        periods=1,
        as_of_date="2026-03-31",
    )

    assert result["trajectory"][0]["period_end"] == "2026-03-31"


def _seed_weekly_spend(conn, week_start: str, total_cents: int) -> None:
    _seed_single_transaction(conn, week_start, total_cents)


def _seed_single_transaction(conn, posted_at: str, amount_cents: int) -> None:
    category_id = conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Dining Out'"
    ).fetchone()["id"]
    account_id = conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'DCU'"
    ).fetchone()["id"]
    batch_id = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 AS id FROM finance_import_batches").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (?, ?, 'csv', ?, ?)
        """,
        (batch_id, account_id, f"seed-{batch_id}.csv", f"fp-{batch_id}"),
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, ?, ?, 'Meal', 'Cafe', ?, ?, 'manual')
        """,
        (account_id, batch_id, posted_at, -abs(amount_cents), category_id),
    )
