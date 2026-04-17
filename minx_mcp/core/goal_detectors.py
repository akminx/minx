from __future__ import annotations

from datetime import date, timedelta

from minx_mcp.core._utils import slugify
from minx_mcp.core.goal_progress import get_metric_value
from minx_mcp.core.memory_models import DetectorResult
from minx_mcp.core.models import GoalProgress, InsightCandidate, ReadModels
from minx_mcp.money import format_cents

# Drift thresholds for spending goals (cents)
DRIFT_SPEND_ALERT_DELTA = 5_000  # $50 — minimum spend delta to trigger alert severity
DRIFT_SPEND_WARN_DELTA = 2_000  # $20 — minimum spend delta to trigger warning severity
# Drift thresholds for count goals (transaction count)
DRIFT_COUNT_ALERT_DELTA = 4  # 4+ extra transactions → alert
DRIFT_COUNT_WARN_DELTA = 2  # 2+ extra transactions → warning


def detect_goal_drift(read_models: ReadModels) -> DetectorResult:
    insights: list[InsightCandidate] = []
    for goal in read_models.goal_progress:
        if goal.status != "off_track":
            continue
        insights.append(
            InsightCandidate(
                insight_type="core.goal_drift",
                dedupe_key=f"{read_models.timeline.date}:goal_drift:goal-{goal.goal_id}",
                summary=f"{goal.title} is off track.",
                supporting_signals=[goal.summary],
                confidence=0.9,
                severity="warning",
                actionability="action_needed",
                source="detector",
            )
        )
    return DetectorResult(tuple(insights), ())


def detect_category_drift(read_models: ReadModels) -> DetectorResult:
    if read_models.finance_api is None:
        return DetectorResult.empty()

    insights: list[InsightCandidate] = []
    for goal in read_models.goal_progress:
        effective_start = date.fromisoformat(goal.current_start)
        review_date = date.fromisoformat(read_models.timeline.date)
        if review_date < effective_start:
            continue

        span_days = (review_date - effective_start).days + 1
        prior_end = effective_start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=span_days - 1)

        current_value = goal.actual_value
        prior_value = _read_goal_window_value(
            read_models,
            goal,
            prior_start.isoformat(),
            prior_end.isoformat(),
        )
        if prior_value <= 0:
            continue

        delta = current_value - prior_value
        ratio = current_value / prior_value
        severity = _category_drift_severity(goal, ratio, delta)
        if severity is None:
            continue

        filter_label = _goal_filter_label(goal)
        noun = "spending" if goal.metric_type.startswith("sum_") else "activity"
        insights.append(
            InsightCandidate(
                insight_type="finance.category_drift",
                dedupe_key=(
                    f"{read_models.timeline.date}:category_drift:"
                    f"goal-{goal.goal_id}:{slugify(filter_label)}"
                ),
                summary=(
                    f"{filter_label} {noun} is up versus the prior comparable span "
                    f"for {goal.title}."
                ),
                supporting_signals=[
                    f"Current span: {_format_value(goal, current_value)}",
                    f"Prior span: {_format_value(goal, prior_value)}",
                    f"Delta: {_format_delta(goal, delta)} ({ratio:.2f}x prior)",
                    f"Goal: {goal.summary}",
                ],
                confidence=0.82 if severity == "warning" else 0.9,
                severity=severity,
                actionability="suggestion" if severity == "warning" else "action_needed",
                source="detector",
            )
        )
    return DetectorResult(tuple(insights), ())


def detect_goal_finance_risks(read_models: ReadModels) -> DetectorResult:
    insights: list[InsightCandidate] = []
    for goal in read_models.goal_progress:
        if (
            goal.metric_type not in ("sum_below", "count_below")
            or goal.target_value <= 0
            or goal.status == "on_track"
        ):
            continue
        pct = round((goal.actual_value / goal.target_value) * 100)
        if pct < 60:
            continue
        insights.append(
            InsightCandidate(
                insight_type="finance.goal_risk",
                dedupe_key=f"{read_models.timeline.date}:goal-risk:{goal.goal_id}",
                summary=f"{goal.title} is already at {pct}% of its target for this period.",
                supporting_signals=[goal.summary],
                confidence=0.86,
                severity="warning" if pct < 85 else "alert",
                actionability="suggestion" if pct < 85 else "action_needed",
                source="detector",
            )
        )
    return DetectorResult(tuple(insights), ())


def _read_goal_window_value(
    read_models: ReadModels,
    goal: GoalProgress,
    start_date: str,
    end_date: str,
) -> int:
    finance_api = read_models.finance_api
    if finance_api is None:
        return 0
    return get_metric_value(
        finance_api,
        goal.metric_type,
        start_date,
        end_date,
        goal.category_names,
        goal.merchant_names,
        goal.account_names,
    )


def _category_drift_severity(
    goal: GoalProgress,
    ratio: float,
    delta: int,
) -> str | None:
    if goal.metric_type.startswith("sum_"):
        if ratio >= 1.5 and delta >= DRIFT_SPEND_ALERT_DELTA:
            return "alert"
        if ratio >= 1.25 and delta >= DRIFT_SPEND_WARN_DELTA:
            return "warning"
        return None
    if ratio >= 1.5 and delta >= DRIFT_COUNT_ALERT_DELTA:
        return "alert"
    if ratio >= 1.25 and delta >= DRIFT_COUNT_WARN_DELTA:
        return "warning"
    return None


def _format_value(goal: GoalProgress, value: int) -> str:
    if goal.metric_type.startswith("sum_"):
        return format_cents(value)
    return str(value)


def _format_delta(goal: GoalProgress, delta: int) -> str:
    if goal.metric_type.startswith("sum_"):
        return format_cents(delta)
    return str(delta)


def _goal_filter_label(goal: GoalProgress) -> str:
    parts: list[str] = []
    if goal.category_names:
        parts.append(", ".join(goal.category_names))
    if goal.merchant_names:
        parts.append(", ".join(goal.merchant_names))
    if goal.account_names:
        parts.append(", ".join(goal.account_names))
    return " / ".join(parts)
