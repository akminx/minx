from __future__ import annotations

from dataclasses import dataclass

from minx_mcp.core.models import DailyReview

PROTECTED_ATTENTION_AREAS = ("activity", "goals", "open_loops", "spending")
REDACTION_POLICY = "core_default_v1"
ACTIVITY_LOW_MAX = 1
ACTIVITY_MODERATE_MAX = 3
MANY_THRESHOLD = 3
_GOAL_NEEDS_ATTENTION_STATUSES = frozenset({"off_track", "watch"})


@dataclass(frozen=True)
class ProtectedDailyReview:
    date: str
    llm_enriched: bool
    attention_areas: list[str]
    activity_level: str
    goal_attention_level: str
    open_loop_level: str
    narrative: str
    next_day_focus: list[str]
    redaction_applied: bool
    redaction_policy: str
    redacted_fields: list[str]
    blocked_fields: list[str]


def build_protected_review(review: DailyReview) -> ProtectedDailyReview:
    activity_level = _bucket_activity(len(review.timeline.entries))
    goals_needing_attention = sum(
        1 for gp in review.goal_progress if gp.status in _GOAL_NEEDS_ATTENTION_STATUSES
    )
    goal_attention_level = _bucket_many_some_none(goals_needing_attention)
    open_loop_level = _bucket_many_some_none(len(review.open_loops.loops))
    attention_areas = _build_attention_areas(
        review,
        activity_level,
        goal_attention_level,
        open_loop_level,
    )
    return ProtectedDailyReview(
        date=review.date,
        llm_enriched=review.llm_enriched,
        attention_areas=attention_areas,
        activity_level=activity_level,
        goal_attention_level=goal_attention_level,
        open_loop_level=open_loop_level,
        narrative=_build_protected_narrative(
            attention_areas,
            activity_level,
            goal_attention_level,
            open_loop_level,
        ),
        next_day_focus=_build_protected_focus(
            attention_areas,
            goal_attention_level,
            open_loop_level,
        ),
        redaction_applied=True,
        redaction_policy=REDACTION_POLICY,
        redacted_fields=[
            "narrative",
            "next_day_focus",
        ],
        blocked_fields=[
            "timeline",
            "spending",
            "open_loops",
            "goal_progress",
            "insights",
            "summary",
            "supporting_signals",
            "dedupe_key",
            "source",
            "goal_titles",
            "goal_notes",
            "goal_filters",
            "markdown",
        ],
    )


def _bucket_activity(entry_count: int) -> str:
    if entry_count <= 0:
        return "none"
    if entry_count <= ACTIVITY_LOW_MAX:
        return "low"
    if entry_count <= ACTIVITY_MODERATE_MAX:
        return "moderate"
    return "high"


def _bucket_many_some_none(count: int) -> str:
    if count <= 0:
        return "none"
    if count < MANY_THRESHOLD:
        return "some"
    return "many"


def _build_attention_areas(
    review: DailyReview,
    activity_level: str,
    goal_attention_level: str,
    open_loop_level: str,
) -> list[str]:
    areas: list[str] = []
    if activity_level != "none":
        areas.append("activity")
    if review.spending.uncategorized_total_cents > 0:
        areas.append("spending")
    if goal_attention_level != "none":
        areas.append("goals")
    if open_loop_level != "none":
        areas.append("open_loops")
    return areas


def _build_protected_narrative(
    attention_areas: list[str],
    activity_level: str,
    goal_attention_level: str,
    open_loop_level: str,
) -> str:
    if not attention_areas:
        return "Protected summary: no flagged areas."

    parts: list[str] = []
    if "activity" in attention_areas:
        parts.append(f"{activity_level} activity")
    if goal_attention_level != "none":
        parts.append(f"{goal_attention_level} goal attention")
    if open_loop_level != "none":
        parts.append(f"{open_loop_level} open-loop attention")
    if "spending" in attention_areas:
        parts.append("spending activity")

    return "Protected summary: " + ", ".join(parts) + "."


def _build_protected_focus(
    attention_areas: list[str],
    goal_attention_level: str,
    open_loop_level: str,
) -> list[str]:
    focus: list[str] = []
    if open_loop_level != "none":
        focus.append("Review outstanding items")
    if goal_attention_level != "none":
        focus.append("Check active goals")
    if "spending" in attention_areas:
        focus.append("Review protected spending summary")
    if not focus and "activity" in attention_areas:
        focus.append("Review today's activity")
    return focus
