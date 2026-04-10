from __future__ import annotations


def build_goal_capture_context(
    message: str,
    review_date: str,
    active_goals: list[object],
    category_names: list[str],
    merchant_names: list[str],
) -> dict[str, object]:
    return {
        "message": message,
        "review_date": review_date,
        "active_goals": [
            {
                "id": getattr(g, "id", None),
                "title": getattr(g, "title", None),
                "status": getattr(g, "status", None),
                "period": getattr(g, "period", None),
                "target_value": getattr(g, "target_value", None),
            }
            for g in active_goals[:10]
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
) -> dict[str, object]:
    return {
        "message": message,
        "review_date": review_date,
        "category_names": category_names[:100],
        "merchant_names": merchant_names[:100],
        "account_names": account_names[:20],
    }
