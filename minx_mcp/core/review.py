from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection

from minx_mcp.core.detectors import DETECTORS
from minx_mcp.core.llm import LLMError, create_llm
from minx_mcp.core.models import (
    DailyReview,
    DurabilitySinkFailure,
    InsightCandidate,
    ReadModels,
    ReviewContext,
    ReviewDurabilityError,
)
from minx_mcp.core.read_models import build_read_models
from minx_mcp.db import get_connection
from minx_mcp.finance.read_api import FinanceReadAPI
from minx_mcp.money import format_cents

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 30.0

_SEVERITY_PRIORITY = {
    "alert": 0,
    "warning": 1,
    "info": 2,
}


async def generate_daily_review(
    date: str,
    ctx: ReviewContext,
    force: bool = False,
) -> DailyReview:
    """Build and durably persist a daily review.

    Returns the in-memory artifact only when detector insight persistence and
    vault note writing both succeed. Raises `ReviewDurabilityError` after the
    artifact is built if either durability step fails; callers can inspect
    `exc.artifact` and `exc.failures`.
    """
    conn = get_connection(Path(ctx.db_path))
    try:
        read_models = _build_review_models(conn, date, ctx)
        detector_insights = _sorted_insights(_run_detectors(read_models))

        llm = ctx.llm or create_llm(db_path=ctx.db_path)
        narrative: str
        next_day_focus: list[str]
        insights = list(detector_insights)
        llm_enriched = False

        if llm is not None:
            try:
                llm_result = await asyncio.wait_for(
                    llm.evaluate_review(
                        timeline=read_models.timeline,
                        spending=read_models.spending,
                        open_loops=read_models.open_loops,
                        detector_insights=detector_insights,
                    ),
                    timeout=LLM_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("LLM review timed out for %s", date)
            except LLMError as exc:
                logger.warning("LLM review failed for %s: %s", date, exc)
            except Exception as exc:
                logger.warning("Unexpected LLM review failure for %s: %s", date, exc)
            else:
                insights = detector_insights + _sorted_insights(
                    llm_result.additional_insights
                )
                narrative = llm_result.narrative
                next_day_focus = llm_result.next_day_focus
                llm_enriched = True

        if not llm_enriched:
            narrative = _build_fallback_narrative(read_models)
            next_day_focus = _build_fallback_focus(read_models)

        artifact = DailyReview(
            date=date,
            timeline=read_models.timeline,
            spending=read_models.spending,
            open_loops=read_models.open_loops,
            insights=insights,
            narrative=narrative,
            next_day_focus=next_day_focus,
            llm_enriched=llm_enriched,
        )

        failures: list[DurabilitySinkFailure] = []

        try:
            _persist_detector_insights(conn, date, detector_insights, force=force)
        except Exception as exc:
            logger.warning("Detector insight persistence failed for %s: %s", date, exc)
            failures.append(DurabilitySinkFailure("detector_insights", exc))

        try:
            ctx.vault_writer.write_markdown(
                _review_note_path(date),
                render_daily_review_markdown(artifact),
            )
        except Exception as exc:
            logger.warning("Vault write failed for %s: %s", date, exc)
            failures.append(DurabilitySinkFailure("vault_note", exc))

        if failures:
            raise ReviewDurabilityError(artifact, failures)

        return artifact
    finally:
        conn.close()


def render_daily_review_markdown(review: DailyReview) -> str:
    timeline_lines = (
        [f"- {entry.occurred_at} | {entry.summary}" for entry in review.timeline.entries]
        or ["- No events recorded."]
    )
    spending_lines = [
        f"- Total spent: {format_cents(review.spending.total_spent_cents)}",
        (
            "- Top categories: "
            + (
                ", ".join(
                    f"{name} ({format_cents(total)})"
                    for name, total in review.spending.by_category.items()
                )
                if review.spending.by_category
                else "None"
            )
        ),
        (
            "- Top merchants: "
            + (
                ", ".join(
                    f"{merchant} ({format_cents(total)})"
                    for merchant, total in review.spending.top_merchants
                )
                if review.spending.top_merchants
                else "None"
            )
        ),
        (
            "- Vs prior week: "
            + (
                f"{review.spending.vs_prior_week_pct:.1f}%"
                if review.spending.vs_prior_week_pct is not None
                else "N/A"
            )
        ),
    ]
    insight_lines = (
        [_format_insight_line(insight) for insight in review.insights]
        or ["- No insights."]
    )
    open_loop_lines = (
        [
            f"- [{loop.severity}] {loop.description}"
            for loop in review.open_loops.loops
        ]
        or ["- No open loops."]
    )
    focus_lines = (
        [f"- {item}" for item in review.next_day_focus] or ["- None."]
    )
    generated_at = (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )

    return "\n".join(
        [
            f"# Daily Review — {review.date}",
            "",
            "## Summary",
            review.narrative,
            "",
            "## Timeline",
            *timeline_lines,
            "",
            "## Spending",
            *spending_lines,
            "",
            "## Insights",
            *insight_lines,
            "",
            "## Open Loops",
            *open_loop_lines,
            "",
            "## Tomorrow's Focus",
            *focus_lines,
            "",
            "---",
            f"Generated: {generated_at} | LLM enriched: {'yes' if review.llm_enriched else 'no'}",
            "",
        ]
    )


def _build_review_models(conn: Connection, date: str, ctx: ReviewContext) -> ReadModels:
    finance_api = _resolve_finance_api(conn, ctx.finance_api)
    return build_read_models(
        conn,
        date,
        finance_api=finance_api,
    )


def _run_detectors(read_models: ReadModels) -> list[InsightCandidate]:
    insights: list[InsightCandidate] = []
    for detector in DETECTORS:
        insights.extend(detector(read_models))
    return insights


def _sorted_insights(insights: list[InsightCandidate]) -> list[InsightCandidate]:
    return sorted(
        insights,
        key=lambda insight: (
            _SEVERITY_PRIORITY.get(insight.severity, 99),
            -insight.confidence,
            insight.insight_type,
            insight.dedupe_key,
        ),
    )


def _build_fallback_narrative(read_models: ReadModels) -> str:
    if (
        not read_models.timeline.entries
        and read_models.spending.total_spent_cents == 0
        and not read_models.open_loops.loops
    ):
        return f"Quiet day. No notable events or open loops for {read_models.timeline.date}."

    top_category = None
    if read_models.spending.by_category:
        top_category = max(
            read_models.spending.by_category.items(),
            key=lambda item: (item[1], item[0]),
        )[0]
    loop_count = len(read_models.open_loops.loops)
    narrative_parts = [
        f"Today you spent {format_cents(read_models.spending.total_spent_cents)}.",
    ]
    if top_category is not None:
        narrative_parts.append(f"Top category: {top_category}.")
    if loop_count == 0:
        narrative_parts.append("No items need attention.")
    elif loop_count == 1:
        narrative_parts.append("1 item needs attention.")
    else:
        narrative_parts.append(f"{loop_count} items need attention.")
    return " ".join(narrative_parts)


def _build_fallback_focus(read_models: ReadModels) -> list[str]:
    focus: list[str] = []
    for loop in read_models.open_loops.loops:
        if loop.loop_type == "uncategorized_transactions" and loop.count is not None:
            focus.append(f"Categorize {loop.count} uncategorized transactions")
            continue

        job_id = _extract_job_id(loop.description)
        if job_id is not None:
            focus.append(f"Check finance import job {job_id}")
    return focus


def _format_insight_line(insight: InsightCandidate) -> str:
    extra_signals = [
        signal for signal in insight.supporting_signals if signal != insight.summary
    ]
    details = f" ({'; '.join(extra_signals)})" if extra_signals else ""
    return (
        f"- [{insight.severity}] {insight.insight_type}: {insight.summary}{details}"
    )


def _resolve_finance_api(conn: Connection, finance_api):
    # Rebind the default concrete API to the review connection so one run sees one DB snapshot.
    if isinstance(finance_api, FinanceReadAPI):
        return FinanceReadAPI(conn)
    return finance_api


def _persist_detector_insights(
    conn: Connection,
    review_date: str,
    insights: list[InsightCandidate],
    *,
    force: bool,
) -> None:
    if force:
        _replace_detector_insights(conn, review_date, insights)
        return

    savepoint = "persist_detector_insights"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        _insert_detector_insights(conn, review_date, insights)
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        conn.commit()


def _replace_detector_insights(
    conn: Connection,
    review_date: str,
    insights: list[InsightCandidate],
) -> None:
    savepoint = "replace_detector_insights"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        conn.execute(
            "DELETE FROM insights WHERE review_date = ? AND source = 'detector'",
            (review_date,),
        )
        _insert_detector_insights(conn, review_date, insights)
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        conn.commit()


def _insert_detector_insights(
    conn: Connection,
    review_date: str,
    insights: list[InsightCandidate],
) -> None:
    rows = [
        (
            insight.insight_type,
            insight.dedupe_key,
            insight.summary,
            json.dumps(insight.supporting_signals),
            insight.confidence,
            insight.severity,
            insight.actionability,
            insight.source,
            review_date,
        )
        for insight in insights
        if insight.source == "detector"
    ]
    if not rows:
        return

    conn.executemany(
        """
        INSERT OR IGNORE INTO insights (
            insight_type,
            dedupe_key,
            summary,
            supporting_signals,
            confidence,
            severity,
            actionability,
            source,
            review_date,
            expires_at,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, datetime('now'))
        """,
        rows,
    )


def _extract_job_id(description: str) -> str | None:
    match = re.search(r"\bjob\s+([A-Za-z0-9._:-]+)\b", description)
    if not match:
        return None
    return match.group(1)


def _review_note_path(date: str) -> str:
    return f"Minx/Reviews/{date}-daily-review.md"
