from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict

from minx_mcp.core.goal_models import GoalRecord


class GoalPromptGoal(TypedDict):
    id: int
    title: str
    status: str
    period: str
    target_value: int


class GoalCaptureContext(TypedDict):
    message: str
    review_date: str
    active_goals: list[GoalPromptGoal]
    category_names: list[str]
    merchant_names: list[str]


class FinanceQueryContext(TypedDict):
    message: str
    review_date: str
    category_names: list[str]
    merchant_names: list[str]
    account_names: list[str]


def build_goal_capture_context(
    message: str,
    review_date: str,
    active_goals: Sequence[GoalRecord],
    category_names: list[str],
    merchant_names: list[str],
) -> GoalCaptureContext:
    return {
        "message": message,
        "review_date": review_date,
        "active_goals": [
            {
                "id": goal.id,
                "title": goal.title,
                "status": goal.status,
                "period": goal.period,
                "target_value": goal.target_value,
            }
            for goal in active_goals[:10]
        ],
        "category_names": category_names[:50],
        "merchant_names": merchant_names[:50],
    }


def build_finance_query_context(
    message: str,
    review_date: str,
    category_names: list[str],
    merchant_names: list[str],
    account_names: list[str],
) -> FinanceQueryContext:
    return {
        "message": message,
        "review_date": review_date,
        "category_names": category_names[:100],
        "merchant_names": merchant_names[:100],
        "account_names": account_names[:20],
    }
