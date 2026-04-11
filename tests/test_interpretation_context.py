from __future__ import annotations

from minx_mcp.core.interpretation.context import (
    build_finance_query_context,
    build_goal_capture_context,
)
from minx_mcp.core.models import GoalRecord


def _goal_record(goal_id: int) -> GoalRecord:
    return GoalRecord(
        id=goal_id,
        goal_type="spending_cap",
        title=f"Goal {goal_id}",
        status="active",
        metric_type="sum_below",
        target_value=10000,
        period="monthly",
        domain="finance",
        category_names=["Dining Out"],
        merchant_names=[],
        account_names=[],
        starts_on="2026-03-01",
        ends_on=None,
        notes=None,
        created_at="2026-03-01 00:00:00",
        updated_at="2026-03-01 00:00:00",
    )


# ---------------------------------------------------------------------------
# build_goal_capture_context
# ---------------------------------------------------------------------------

def test_build_goal_capture_context_caps_goals_at_10():
    goals = [_goal_record(i) for i in range(1, 12)]

    ctx = build_goal_capture_context(
        message="test",
        review_date="2026-03-15",
        active_goals=goals,
        category_names=["Dining Out"],
        merchant_names=["Cafe"],
    )

    assert len(ctx["active_goals"]) == 10


def test_build_goal_capture_context_caps_categories_at_50():
    categories = [f"Category {i}" for i in range(51)]

    ctx = build_goal_capture_context(
        message="test",
        review_date="2026-03-15",
        active_goals=[],
        category_names=categories,
        merchant_names=[],
    )

    assert len(ctx["category_names"]) == 50


def test_build_goal_capture_context_fewer_than_limits_passes_through():
    goals = [_goal_record(i) for i in range(1, 4)]
    categories = ["A", "B", "C"]
    merchants = ["M1", "M2"]

    ctx = build_goal_capture_context(
        message="hello",
        review_date="2026-04-01",
        active_goals=goals,
        category_names=categories,
        merchant_names=merchants,
    )

    assert len(ctx["active_goals"]) == 3
    assert ctx["category_names"] == categories
    assert ctx["merchant_names"] == merchants


# ---------------------------------------------------------------------------
# build_finance_query_context
# ---------------------------------------------------------------------------

def test_build_finance_query_context_caps_categories_at_100():
    categories = [f"Cat {i}" for i in range(101)]

    ctx = build_finance_query_context(
        message="test",
        review_date="2026-03-15",
        category_names=categories,
        merchant_names=[],
        account_names=[],
    )

    assert len(ctx["category_names"]) == 100


def test_build_finance_query_context_caps_accounts_at_20():
    accounts = [f"Account {i}" for i in range(21)]

    ctx = build_finance_query_context(
        message="test",
        review_date="2026-03-15",
        category_names=[],
        merchant_names=[],
        account_names=accounts,
    )

    assert len(ctx["account_names"]) == 20
