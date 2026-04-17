from __future__ import annotations

import re

from minx_mcp.core.models import GoalRecord

_SUPPORTED_CREATE_GOAL_TYPE = "spending_cap"
_SUPPORTED_CREATE_METRIC_TYPE = "sum_below"
_SUPPORTED_CREATE_DOMAIN = "finance"
_SUPPORTED_UPDATE_STATUSES = {"active", "paused"}


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _contains_any_word(haystack: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", haystack) for word in words)


def _contains_exact_phrase(haystack: str, needle: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack))


def _build_create_payload(
    *,
    subject: str,
    period: str,
    starts_on: str,
    target_value: int,
) -> dict[str, object]:
    return {
        "goal_type": _SUPPORTED_CREATE_GOAL_TYPE,
        "title": _build_create_title(subject),
        "metric_type": _SUPPORTED_CREATE_METRIC_TYPE,
        "target_value": target_value,
        "period": period,
        "domain": _SUPPORTED_CREATE_DOMAIN,
        "category_names": [],
        "merchant_names": [],
        "account_names": [],
        "starts_on": starts_on,
        "ends_on": None,
        "notes": None,
    }


def _build_create_title(subject: str) -> str:
    return f"{subject} Spending Cap"


def _summarize_goal_filters(goal: GoalRecord) -> str:
    if goal.category_names:
        return f"category_names={goal.category_names!r}"
    if goal.merchant_names:
        return f"merchant_names={goal.merchant_names!r}"
    if goal.account_names:
        return f"account_names={goal.account_names!r}"
    return "all spending"


def _supported_conversational_goal(goal: GoalRecord) -> bool:
    return (
        goal.goal_type == _SUPPORTED_CREATE_GOAL_TYPE
        and goal.metric_type == _SUPPORTED_CREATE_METRIC_TYPE
        and goal.domain == _SUPPORTED_CREATE_DOMAIN
        and goal.status in _SUPPORTED_UPDATE_STATUSES
    )
