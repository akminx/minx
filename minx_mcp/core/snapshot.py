from __future__ import annotations

import json
import logging
from pathlib import Path
from sqlite3 import Connection

from minx_mcp.core.detectors import DETECTORS
from minx_mcp.core.memory_models import DetectorResult, MemoryProposal
from minx_mcp.core.memory_service import MemoryService
from minx_mcp.core.models import (
    DailySnapshot,
    DurabilitySinkFailure,
    InsightCandidate,
    PersistenceWarning,
    ReadModels,
    SnapshotContext,
)
from minx_mcp.core.read_models import build_read_models
from minx_mcp.db import scoped_connection
from minx_mcp.finance.read_api import FinanceReadAPI

logger = logging.getLogger(__name__)

_SEVERITY_PRIORITY = {
    "alert": 0,
    "warning": 1,
    "info": 2,
}

_GOAL_STATUS_SEVERITY = {
    "off_track": "warning",
    "watch": "info",
}


async def build_daily_snapshot(
    review_date: str,
    ctx: SnapshotContext,
    *,
    force: bool = False,
) -> DailySnapshot:
    with scoped_connection(Path(ctx.db_path)) as conn:
        read_models = _build_snapshot_models(conn, review_date, ctx)
        detector_run = _run_detectors(read_models)
        memory_service = MemoryService(Path(ctx.db_path), conn=conn)
        memory_service.ingest_proposals(detector_run.memory_proposals, actor="detector")
        # TODO(slice6-6d): expose memory context in DailySnapshot (persisted via ingest above).
        detector_signals = _sorted_insights(list(detector_run.insights))
        warning = _persist_warning(conn, review_date, detector_signals, force=force)
        return DailySnapshot(
            date=review_date,
            timeline=read_models.timeline,
            spending=read_models.spending,
            open_loops=read_models.open_loops,
            goal_progress=read_models.goal_progress,
            signals=detector_signals,
            attention_items=_build_attention_items(read_models, detector_signals),
            nutrition=read_models.nutrition,
            training=read_models.training,
            persistence_warning=warning,
        )


def _build_snapshot_models(
    conn: Connection,
    review_date: str,
    ctx: SnapshotContext,
) -> ReadModels:
    finance_api = ctx.finance_api or FinanceReadAPI(conn)
    return build_read_models(
        conn,
        review_date,
        finance_api=finance_api,
        meals_api=ctx.meals_api,
        training_api=ctx.training_api,
    )


def _run_detectors(read_models: ReadModels) -> DetectorResult:
    insights: list[InsightCandidate] = []
    memory_proposals: list[MemoryProposal] = []
    for detector in DETECTORS:
        if not detector.enabled_by_default:
            continue
        result = detector.fn(read_models)
        insights.extend(result.insights)
        memory_proposals.extend(result.memory_proposals)
    return DetectorResult(tuple(insights), tuple(memory_proposals))


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


def _build_attention_items(
    read_models: ReadModels,
    detector_signals: list[InsightCandidate],
) -> list[str]:
    ranked: list[tuple[int, int, str]] = []
    for signal in detector_signals:
        ranked.append((_SEVERITY_PRIORITY.get(signal.severity, 99), 0, signal.summary))
    for loop in read_models.open_loops.loops:
        ranked.append((_SEVERITY_PRIORITY.get(loop.severity, 99), 1, loop.description))
    for goal in read_models.goal_progress:
        mapped = _GOAL_STATUS_SEVERITY.get(goal.status)
        if mapped is None:
            continue
        status_text = goal.status.replace("_", " ")
        ranked.append((_SEVERITY_PRIORITY.get(mapped, 99), 2, f"{goal.title} is {status_text}."))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[2] for item in ranked]


def _persist_warning(
    conn: Connection,
    review_date: str,
    insights: list[InsightCandidate],
    *,
    force: bool,
) -> PersistenceWarning | None:
    try:
        _persist_detector_insights(conn, review_date, insights, force=force)
    except Exception as exc:
        # Snapshot generation should survive any durability sink failure.
        logger.warning("Detector insight persistence failed for %s: %s", review_date, exc)
        failure = DurabilitySinkFailure("detector_insights", exc)
        return PersistenceWarning(
            sink=failure.sink,
            message=(
                "Detector insight persistence failed; snapshot data may be fresher "
                "than stored history."
            ),
        )
    return None


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
    except (
        Exception
    ):  # Broad except is intentional: any failure must roll back the savepoint before re-raising
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
    except (
        Exception
    ):  # Broad except is intentional: any failure must roll back the savepoint before re-raising
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
        INSERT INTO insights (
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
        ON CONFLICT(review_date, insight_type, dedupe_key) DO UPDATE SET
            summary = excluded.summary,
            supporting_signals = excluded.supporting_signals,
            confidence = excluded.confidence,
            severity = excluded.severity,
            actionability = excluded.actionability,
            source = excluded.source,
            created_at = excluded.created_at
        """,
        rows,
    )
