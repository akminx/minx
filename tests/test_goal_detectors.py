from __future__ import annotations

from minx_mcp.core.goal_detectors import detect_goal_drift, detect_goal_finance_risks
from minx_mcp.core.models import (
    DailyTimeline,
    GoalProgress,
    OpenLoopsSnapshot,
    ReadModels,
    SpendingSnapshot,
)


def _make_read_models(goal_progress_list: list[GoalProgress]) -> ReadModels:
    return ReadModels(
        timeline=DailyTimeline(date="2026-04-12", entries=[]),
        spending=SpendingSnapshot(
            date="2026-04-12",
            total_spent_cents=0,
            by_category={},
            top_merchants=[],
            vs_prior_week_pct=None,
            uncategorized_count=0,
            uncategorized_total_cents=0,
        ),
        open_loops=OpenLoopsSnapshot(date="2026-04-12", loops=[]),
        goal_progress=goal_progress_list,
    )


def _make_goal(
    *,
    goal_id: int = 1,
    title: str = "Test goal",
    metric_type: str = "sum_below",
    target_value: int = 10_000,
    actual_value: int,
    status: str = "watch",
) -> GoalProgress:
    remaining = max(0, target_value - actual_value)
    return GoalProgress(
        goal_id=goal_id,
        title=title,
        metric_type=metric_type,
        target_value=target_value,
        actual_value=actual_value,
        remaining_value=remaining,
        current_start="2026-04-01",
        current_end="2026-04-30",
        status=status,
        summary=f"{actual_value} of {target_value}",
        category_names=[],
        merchant_names=[],
        account_names=[],
    )


def test_finance_risk_fires_at_68_percent():
    goal = _make_goal(target_value=10_000, actual_value=6_800, status="watch")
    read_models = _make_read_models([goal])

    insights = detect_goal_finance_risks(read_models).insights

    assert len(insights) == 1
    assert insights[0].severity == "warning"
    assert insights[0].actionability == "suggestion"
    assert "68%" in insights[0].summary


def test_finance_risk_fires_at_85_percent_alert():
    goal = _make_goal(target_value=10_000, actual_value=8_500, status="watch")
    read_models = _make_read_models([goal])

    insights = detect_goal_finance_risks(read_models).insights

    assert len(insights) == 1
    assert insights[0].severity == "alert"
    assert insights[0].actionability == "action_needed"
    assert "85%" in insights[0].summary


def test_finance_risk_skips_on_track():
    goal = _make_goal(target_value=10_000, actual_value=6_800, status="on_track")
    read_models = _make_read_models([goal])

    assert detect_goal_finance_risks(read_models).insights == ()


def test_finance_risk_skips_below_60_percent():
    goal = _make_goal(target_value=10_000, actual_value=5_000, status="watch")
    read_models = _make_read_models([goal])

    assert detect_goal_finance_risks(read_models).insights == ()


def test_finance_risk_fires_for_count_below():
    goal = _make_goal(
        metric_type="count_below",
        target_value=10,
        actual_value=7,
        status="watch",
    )
    read_models = _make_read_models([goal])

    insights = detect_goal_finance_risks(read_models).insights

    assert len(insights) == 1
    assert insights[0].severity == "warning"
    assert insights[0].actionability == "suggestion"


def test_finance_risk_skips_sum_above():
    goal = _make_goal(
        metric_type="sum_above", target_value=10_000, actual_value=8_500, status="watch"
    )
    read_models = _make_read_models([goal])

    assert detect_goal_finance_risks(read_models).insights == ()


def test_goal_drift_fires_for_off_track():
    goal = _make_goal(target_value=10_000, actual_value=11_000, status="off_track")
    read_models = _make_read_models([goal])

    insights = detect_goal_drift(read_models).insights

    assert len(insights) == 1
    assert insights[0].insight_type == "core.goal_drift"
    assert insights[0].severity == "warning"
    assert insights[0].confidence == 0.9


def test_goal_drift_skips_on_track():
    goal = _make_goal(target_value=10_000, actual_value=3_000, status="on_track")
    read_models = _make_read_models([goal])

    assert detect_goal_drift(read_models).insights == ()
