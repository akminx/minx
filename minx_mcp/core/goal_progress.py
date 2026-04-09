from __future__ import annotations

from datetime import date, timedelta

from minx_mcp.core.models import FinanceReadInterface, GoalProgress, GoalRecord
from minx_mcp.money import format_cents


def build_goal_progress(
    review_date: str,
    goals: list[GoalRecord],
    finance_api: FinanceReadInterface,
) -> list[GoalProgress]:
    progress: list[GoalProgress] = []
    for goal in goals:
        goal_progress = build_progress_for_goal(review_date, goal, finance_api)
        if goal_progress is not None:
            progress.append(goal_progress)
    return progress


def build_progress_for_goal(
    review_date: str,
    goal: GoalRecord,
    finance_api: FinanceReadInterface,
) -> GoalProgress | None:
    if not _is_review_date_within_goal_lifetime(goal, review_date):
        return None

    current_start, current_end = _effective_window(goal, review_date)
    review_point = min(date.fromisoformat(review_date), date.fromisoformat(current_end))
    measured_end = review_point.isoformat()
    total = finance_api.get_filtered_spending_total(
        current_start,
        measured_end,
        category_names=goal.category_names or None,
        merchant_names=goal.merchant_names or None,
        account_names=goal.account_names or None,
    )
    count = finance_api.get_filtered_transaction_count(
        current_start,
        measured_end,
        category_names=goal.category_names or None,
        merchant_names=goal.merchant_names or None,
        account_names=goal.account_names or None,
    )
    actual = total if goal.metric_type.startswith("sum_") else count
    return _progress_for_goal(
        goal,
        actual,
        current_start,
        current_end,
        review_point.isoformat(),
    )


def _is_review_date_within_goal_lifetime(goal: GoalRecord, review_date: str) -> bool:
    review_point = date.fromisoformat(review_date)
    if review_point < date.fromisoformat(goal.starts_on):
        return False
    if goal.ends_on is not None and review_point > date.fromisoformat(goal.ends_on):
        return False
    return True


def _effective_window(goal: GoalRecord, review_date: str) -> tuple[str, str]:
    natural_start, natural_end = _period_window(goal.period, review_date)
    effective_start = max(
        date.fromisoformat(natural_start),
        date.fromisoformat(goal.starts_on),
    )
    effective_end = date.fromisoformat(natural_end)
    if goal.ends_on is not None:
        effective_end = min(effective_end, date.fromisoformat(goal.ends_on))
    return effective_start.isoformat(), effective_end.isoformat()


def _period_window(period: str, review_date: str) -> tuple[str, str]:
    rd = date.fromisoformat(review_date)
    if period == "daily":
        return review_date, review_date
    if period == "weekly":
        start = rd - timedelta(days=rd.weekday())
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat()
    if period == "monthly":
        start = rd.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)
        return start.isoformat(), end.isoformat()
    if period == "rolling_28d":
        start = rd - timedelta(days=27)
        return start.isoformat(), review_date
    return review_date, review_date


def _progress_for_goal(
    goal: GoalRecord,
    actual: int,
    current_start: str,
    current_end: str,
    review_date: str,
) -> GoalProgress:
    status = _compute_status(goal, actual, current_start, current_end, review_date)
    remaining = _compute_remaining(goal, actual)
    summary = _compute_summary(
        goal,
        actual,
        remaining,
        status,
        current_start,
        current_end,
        review_date,
    )
    return GoalProgress(
        goal_id=goal.id,
        title=goal.title,
        metric_type=goal.metric_type,
        target_value=goal.target_value,
        actual_value=actual,
        remaining_value=remaining,
        current_start=current_start,
        current_end=current_end,
        status=status,
        summary=summary,
        category_names=goal.category_names,
        merchant_names=goal.merchant_names,
        account_names=goal.account_names,
    )


def _compute_remaining(goal: GoalRecord, actual: int) -> int | None:
    if goal.metric_type in ("sum_below", "count_below"):
        return max(goal.target_value - actual, 0)
    return None


def _compute_status(
    goal: GoalRecord,
    actual: int,
    current_start: str,
    current_end: str,
    review_date: str,
) -> str:
    if goal.metric_type in ("sum_below", "count_below"):
        if actual >= goal.target_value:
            return "off_track"
        elapsed_fraction = _elapsed_fraction(current_start, current_end, review_date)
        expected_at_this_point = goal.target_value * elapsed_fraction
        if actual > expected_at_this_point * 0.9:
            return "watch"
        return "on_track"
    if goal.metric_type in ("sum_above", "count_above"):
        if actual >= goal.target_value:
            return "met"
        elapsed_fraction = _elapsed_fraction(current_start, current_end, review_date)
        expected_at_this_point = goal.target_value * elapsed_fraction
        if actual < expected_at_this_point * 0.7:
            return "off_track"
        if actual < expected_at_this_point * 0.9:
            return "watch"
        return "on_track"
    return "on_track"


def _elapsed_fraction(start: str, end: str, current: str) -> float:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    current_date = date.fromisoformat(current)
    total_days = (end_date - start_date).days + 1
    elapsed_days = (current_date - start_date).days + 1
    if total_days <= 0:
        return 1.0
    return min(elapsed_days / total_days, 1.0)


def _compute_summary(
    goal: GoalRecord,
    actual: int,
    remaining: int | None,
    status: str,
    current_start: str,
    current_end: str,
    review_date: str,
) -> str:
    is_money = goal.metric_type.startswith("sum_")
    actual_str = format_cents(actual) if is_money else str(actual)
    target_str = format_cents(goal.target_value) if is_money else str(goal.target_value)

    if status == "met":
        return f"Met! {actual_str} against target of {target_str}."
    if goal.metric_type in ("sum_above", "count_above") and status == "watch":
        return f"Watch: {actual_str} of {target_str} - below target pace."
    if status == "on_track":
        return f"On track: {actual_str} of {target_str}."
    if status == "watch":
        return f"Watch: {actual_str} of {target_str} — approaching limit."
    remaining_str = (
        format_cents(remaining) if is_money and remaining is not None
        else str(remaining) if remaining is not None
        else ""
    )
    remaining_part = f" {remaining_str} remaining." if remaining_str else "."
    return f"Off track: {actual_str} of {target_str}{remaining_part}"
