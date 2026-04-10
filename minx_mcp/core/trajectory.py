from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.goal_progress import _period_window, _progress_for_goal
from minx_mcp.core.goals import GoalService
from minx_mcp.db import get_connection
from minx_mcp.finance.read_api import FinanceReadAPI


def get_goal_trajectory(
    db_path: str | Path,
    *,
    goal_id: int,
    periods: int = 4,
    as_of_date: str | None = None,
) -> dict[str, object]:
    if periods < 1 or periods > 12:
        raise InvalidInputError("periods must be between 1 and 12")
    effective_as_of = as_of_date or date.today().isoformat()
    try:
        as_of = date.fromisoformat(effective_as_of)
    except ValueError as exc:
        raise InvalidInputError("as_of_date must be a valid ISO date") from exc

    conn = get_connection(Path(db_path))
    try:
        goal = GoalService(conn).get_goal(goal_id)
        finance_api = FinanceReadAPI(conn)
        periods_data = _build_completed_periods(goal, as_of, periods)
        trajectory: list[dict[str, object]] = []
        status_counter: Counter[str] = Counter()
        scores: list[float] = []
        for period_start, period_end in periods_data:
            if period_start < date.fromisoformat(goal.starts_on):
                continue
            if goal.ends_on is not None and period_end > date.fromisoformat(goal.ends_on):
                continue
            actual = _read_value(
                finance_api,
                goal.metric_type,
                period_start.isoformat(),
                period_end.isoformat(),
                goal.category_names,
                goal.merchant_names,
                goal.account_names,
            )
            progress = _progress_for_goal(
                goal,
                actual,
                period_start.isoformat(),
                period_end.isoformat(),
                period_end.isoformat(),
            )
            trajectory.append(
                {
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "actual_value": actual,
                    "target_value": goal.target_value,
                    "status": progress.status,
                }
            )
            status_counter[progress.status] += 1
            scores.append(_trend_score(goal.metric_type, actual))
    finally:
        conn.close()

    return {
        "goal": {
            "id": goal.id,
            "title": goal.title,
            "goal_type": goal.goal_type,
            "metric_type": goal.metric_type,
            "target_value": goal.target_value,
            "period": goal.period,
            "status": goal.status,
            "starts_on": goal.starts_on,
            "ends_on": goal.ends_on,
        },
        "as_of_date": effective_as_of,
        "trajectory": trajectory,
        "trend": _compute_trend(scores),
        "status_counts": dict(status_counter),
    }


def _build_completed_periods(goal, as_of: date, periods: int) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    cursor = _latest_completed_end(goal.period, as_of)
    while len(windows) < periods:
        if goal.period == "rolling_28d":
            start = cursor - timedelta(days=27)
            end = cursor
            cursor = cursor - timedelta(days=7)
        elif goal.period == "daily":
            start = cursor
            end = cursor
            cursor = cursor - timedelta(days=1)
        elif goal.period == "weekly":
            start = cursor - timedelta(days=6)
            end = cursor
            cursor = start - timedelta(days=1)
        elif goal.period == "monthly":
            start = cursor.replace(day=1)
            end = cursor
            cursor = start - timedelta(days=1)
        else:
            raise InvalidInputError(f"Unsupported goal period for trajectory: {goal.period}")
        windows.append((start, end))
    windows.reverse()
    return windows


def _latest_completed_end(period: str, as_of: date) -> date:
    if period == "daily":
        return as_of - timedelta(days=1)
    if period == "weekly":
        days_since_sunday = (as_of.weekday() + 1) % 7
        return as_of - timedelta(days=days_since_sunday)
    if period == "monthly":
        first_of_month = as_of.replace(day=1)
        current_month_end = (first_of_month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if as_of == current_month_end:
            return as_of
        return first_of_month - timedelta(days=1)
    if period == "rolling_28d":
        days_since_sunday = (as_of.weekday() + 1) % 7
        return as_of - timedelta(days=days_since_sunday)
    start, end = _period_window(period, as_of.isoformat())
    return date.fromisoformat(end)


def _read_value(
    finance_api: FinanceReadAPI,
    metric_type: str,
    start_date: str,
    end_date: str,
    category_names: list[str],
    merchant_names: list[str],
    account_names: list[str],
) -> int:
    if metric_type.startswith("sum_"):
        return finance_api.get_filtered_spending_total(
            start_date,
            end_date,
            category_names=category_names or None,
            merchant_names=merchant_names or None,
            account_names=account_names or None,
        )
    return finance_api.get_filtered_transaction_count(
        start_date,
        end_date,
        category_names=category_names or None,
        merchant_names=merchant_names or None,
        account_names=account_names or None,
    )


def _trend_score(metric_type: str, actual: int) -> float:
    if metric_type in {"sum_below", "count_below"}:
        return float(actual)
    return float(-actual)


def _compute_trend(scores: list[float]) -> str | None:
    if len(scores) < 2:
        return None
    if scores[-1] < scores[0]:
        return "improving"
    if scores[-1] > scores[0]:
        return "worsening"
    return "stable"
