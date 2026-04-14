from __future__ import annotations

from datetime import date, datetime, timedelta
from sqlite3 import Connection
from typing import Any

from minx_mcp.core.events import Event, query_events
from minx_mcp.core.goal_progress import build_goal_progress
from minx_mcp.core.goals import GoalService
from minx_mcp.core.models import (
    DailyTimeline,
    FinanceReadInterface,
    MealsReadInterface,
    OpenLoop,
    OpenLoopsSnapshot,
    ReadModels,
    SpendingSnapshot,
    TrainingReadInterface,
    TimelineEntry,
)
from minx_mcp.finance.read_api import FinanceReadAPI
from minx_mcp.money import format_cents
from minx_mcp.preferences import get_preference


def build_daily_timeline(conn: Connection, review_date: str) -> DailyTimeline:
    timezone_name = _resolve_timezone_name(conn)
    events = query_events(
        conn,
        start=review_date,
        end=review_date,
        timezone=timezone_name,
        sensitivity="normal",
    )
    entries = [
        TimelineEntry(
            occurred_at=event.occurred_at,
            domain=event.domain,
            event_type=event.event_type,
            summary=_summarize_event(event),
            entity_ref=event.entity_ref,
        )
        for event in events
    ]
    return DailyTimeline(date=review_date, entries=entries)


def build_spending_snapshot(
    conn: Connection,
    review_date: str,
    finance_api: FinanceReadInterface | None = None,
    uncategorized: Any | None = None,
) -> SpendingSnapshot:
    finance_api = finance_api or FinanceReadAPI(conn)
    summary = finance_api.get_spending_summary(review_date, review_date)
    if uncategorized is None:
        uncategorized = finance_api.get_uncategorized(review_date, review_date)

    current_end = date.fromisoformat(review_date)
    current_start = (current_end - timedelta(days=6)).isoformat()
    prior_end = (current_end - timedelta(days=7)).isoformat()
    prior_start = (current_end - timedelta(days=13)).isoformat()
    comparison = finance_api.get_period_comparison(
        current_start,
        review_date,
        prior_start,
        prior_end,
    )

    vs_prior_week_pct = None
    if comparison.prior_total_spent_cents > 0:
        raw_pct = (
            (comparison.current_total_spent_cents - comparison.prior_total_spent_cents)
            / comparison.prior_total_spent_cents
            * 100
        )
        vs_prior_week_pct = round(raw_pct, 2)

    return SpendingSnapshot(
        date=review_date,
        total_spent_cents=summary.total_spent_cents,
        by_category={
            item.category_name: item.total_spent_cents for item in summary.by_category
        },
        top_merchants=[
            (item.merchant, item.total_spent_cents) for item in summary.top_merchants
        ],
        vs_prior_week_pct=vs_prior_week_pct,
        uncategorized_count=uncategorized.transaction_count,
        uncategorized_total_cents=uncategorized.total_spent_cents,
    )


def build_open_loops_snapshot(
    conn: Connection,
    review_date: str,
    finance_api: FinanceReadInterface | None = None,
    uncategorized: Any | None = None,
) -> OpenLoopsSnapshot:
    finance_api = finance_api or FinanceReadAPI(conn)
    if uncategorized is None:
        uncategorized = finance_api.get_uncategorized(review_date, review_date)
    import_job_issues = finance_api.get_import_job_issues()

    loops: list[OpenLoop] = []
    if uncategorized.transaction_count > 0:
        loops.append(
            OpenLoop(
                domain="finance",
                loop_type="uncategorized_transactions",
                description=(
                    f"{uncategorized.transaction_count} uncategorized transactions "
                    f"totaling {format_cents(uncategorized.total_spent_cents)}"
                ),
                count=uncategorized.transaction_count,
                severity="warning" if uncategorized.transaction_count > 20 else "info",
            )
        )

    for issue in import_job_issues:
        source_ref = issue.source_ref or "unknown source"
        if issue.issue_kind == "failed":
            loop_type = "failed_import_job"
            description = f"Import job {issue.job_id} failed for {source_ref}"
        else:
            loop_type = "stale_import_job"
            description = f"Import job {issue.job_id} is stale for {source_ref}"
        loops.append(
            OpenLoop(
                domain="finance",
                loop_type=loop_type,
                description=description,
                count=1,
                severity="warning",
            )
        )

    return OpenLoopsSnapshot(date=review_date, loops=loops)


def build_read_models(
    conn: Connection,
    review_date: str,
    finance_api: FinanceReadInterface | None = None,
    meals_api: MealsReadInterface | None = None,
    training_api: TrainingReadInterface | None = None,
) -> ReadModels:
    finance_api = finance_api or FinanceReadAPI(conn)
    if meals_api is None:
        from minx_mcp.meals.read_api import MealsReadAPI

        meals_api = MealsReadAPI(conn)
    if training_api is None:
        from minx_mcp.training.read_api import TrainingReadAPI

        training_api = TrainingReadAPI(conn)
    goals = GoalService(conn).list_active_goals(review_date)
    uncategorized = finance_api.get_uncategorized(review_date, review_date)
    return ReadModels(
        timeline=build_daily_timeline(conn, review_date),
        spending=build_spending_snapshot(
            conn, review_date, finance_api=finance_api, uncategorized=uncategorized,
        ),
        open_loops=build_open_loops_snapshot(
            conn, review_date, finance_api=finance_api, uncategorized=uncategorized,
        ),
        goal_progress=build_goal_progress(review_date, goals, finance_api),
        nutrition=meals_api.get_nutrition_summary(review_date),
        training=training_api.get_training_summary(review_date),
        finance_api=finance_api,
        meals_api=meals_api,
        training_api=training_api,
    )


def _resolve_timezone_name(conn: Connection) -> str:
    configured = get_preference(conn, "core", "timezone", None)
    if isinstance(configured, str) and configured:
        return configured
    return _get_machine_local_timezone_name()


def _get_machine_local_timezone_name() -> str:
    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    if isinstance(key, str) and key:
        return key
    return "UTC"


def _summarize_event(event: Event) -> str:
    payload = event.payload
    if event.event_type == "finance.transactions_imported":
        return (
            f"Imported {payload['transaction_count']} transactions from "
            f"{payload['account_name']} via {payload['source_kind']} "
            f"(net {format_cents(payload['total_cents'])})"
        )
    if event.event_type == "finance.transactions_categorized":
        categories = ", ".join(payload["categories"])
        return f"Categorized {payload['count']} transactions into {categories}"
    if event.event_type == "finance.report_generated":
        return (
            f"Generated {payload['report_type']} report for "
            f"{payload['period_start']} to {payload['period_end']}"
        )
    if event.event_type == "finance.anomalies_detected":
        return (
            f"Detected {payload['count']} anomalies totaling "
            f"{format_cents(payload['total_cents'])}"
        )
    if event.event_type == "meal.logged":
        calories = payload.get("calories")
        suffix = f" ({calories} cal)" if calories is not None else ""
        return f"Logged {payload.get('meal_kind', 'meal')}{suffix}"
    if event.event_type == "nutrition.day_updated":
        protein = payload.get("protein_grams", "?")
        return f"Nutrition update: {payload['meal_count']} meals, {protein}g protein"
    if event.event_type == "workout.completed":
        return (
            f"Logged workout: {payload.get('set_count', 0)} sets, "
            f"{float(payload.get('total_volume_kg', 0.0)):.0f}kg volume"
        )
    if event.event_type == "training.program_updated":
        state = "active" if payload.get("is_active") else "updated"
        return f"Training program {payload.get('name', 'program')} {state}"
    if event.event_type == "training.milestone_reached":
        return str(payload.get("summary", "Training milestone reached"))
    return event.event_type
