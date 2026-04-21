from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from contextlib import suppress
from dataclasses import asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from sqlite3 import Connection

from minx_mcp.core.detectors import DETECTORS
from minx_mcp.core.memory_models import DetectorResult, MemoryProposal, MemoryRecord
from minx_mcp.core.memory_service import MemoryService
from minx_mcp.core.models import (
    DailySnapshot,
    DurabilitySinkFailure,
    InsightCandidate,
    MemoryContext,
    MemoryContextItem,
    MemoryEventItem,
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
    return await asyncio.to_thread(
        _build_daily_snapshot_sync,
        review_date,
        ctx,
        force=force,
    )


def _build_daily_snapshot_sync(
    review_date: str,
    ctx: SnapshotContext,
    *,
    force: bool = False,
) -> DailySnapshot:
    with scoped_connection(Path(ctx.db_path)) as conn:
        read_models = _build_snapshot_models(conn, review_date, ctx)
        detector_run = _run_detectors(read_models)
        memory_service = MemoryService(Path(ctx.db_path), conn=conn)
        memory_ingest_warning = _ingest_memory_proposals_best_effort(
            review_date,
            memory_service,
            detector_run.memory_proposals,
        )
        memory_context, memory_warning = _build_memory_context_best_effort(memory_service)
        detector_signals = _sorted_insights(list(detector_run.insights))
        warning = _persist_warning(conn, review_date, detector_signals, force=force)
        if warning is None:
            warning = memory_ingest_warning or memory_warning
        attention_items = _build_attention_items(read_models, detector_signals)
        if memory_context.pending_candidate_count:
            attention_items.append(f"{memory_context.pending_candidate_count} memory candidates need review.")
        snapshot = DailySnapshot(
            date=review_date,
            timeline=read_models.timeline,
            spending=read_models.spending,
            open_loops=read_models.open_loops,
            goal_progress=read_models.goal_progress,
            signals=detector_signals,
            attention_items=attention_items,
            nutrition=read_models.nutrition,
            training=read_models.training,
            persistence_warning=warning,
            memory_context=memory_context,
        )
        _persist_snapshot_archive(conn, review_date, snapshot)
        return snapshot


def _build_memory_context_best_effort(
    memory_service: MemoryService,
) -> tuple[MemoryContext, PersistenceWarning | None]:
    try:
        active_memories = memory_service.list_active_memories(limit=100)
        pending = memory_service.list_pending_candidates(limit=500)
        events = _list_recent_memory_events(memory_service.conn)
        return (
            MemoryContext(
                active=[_memory_context_item(memory) for memory in active_memories],
                pending_candidate_count=len(pending),
                recent_events=events,
            ),
            None,
        )
    except Exception as exc:
        logger.warning("Memory context build failed: %s", exc)
        return (
            MemoryContext(active=[], pending_candidate_count=0, recent_events=[]),
            PersistenceWarning(
                sink="memory_context",
                message="Memory context build failed; snapshot omitted memory state.",
            ),
        )


def _memory_context_item(memory: MemoryRecord) -> MemoryContextItem:
    return MemoryContextItem(
        id=memory.id,
        memory_type=memory.memory_type,
        scope=memory.scope,
        subject=memory.subject,
        confidence=memory.confidence,
        payload=memory.payload,
        source=memory.source,
        reason=memory.reason,
        updated_at=memory.updated_at,
    )


def _list_recent_memory_events(conn: Connection, *, limit: int = 50) -> list[MemoryEventItem]:
    rows = conn.execute(
        """
        SELECT id, memory_id, event_type, actor, created_at, payload_json
        FROM memory_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        MemoryEventItem(
            id=int(row["id"]),
            memory_id=int(row["memory_id"]),
            event_type=str(row["event_type"]),
            actor=str(row["actor"]),
            created_at=str(row["created_at"]),
            payload=_parse_event_payload(str(row["payload_json"])),
        )
        for row in rows
    ]


def _parse_event_payload(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ingest_memory_proposals_best_effort(
    review_date: str,
    memory_service: MemoryService,
    proposals: tuple[MemoryProposal, ...],
) -> PersistenceWarning | None:
    try:
        report = memory_service.ingest_proposals(proposals, actor="detector")
    except Exception as exc:
        logger.warning(
            "Memory proposal ingestion failed for %s: %s",
            review_date,
            exc,
            extra={
                "domain": "core",
                "tool": "build_daily_snapshot",
                "success": False,
            },
        )
        return PersistenceWarning(
            sink="memory_proposals",
            message="Memory proposal ingestion failed; snapshot memory state may be incomplete.",
        )
    if report.failures:
        failed = ", ".join(
            f"{failure.memory_type}:{failure.scope}:{failure.subject}" for failure in report.failures[:5]
        )
        suffix = "" if len(report.failures) <= 5 else f" (+{len(report.failures) - 5} more)"
        return PersistenceWarning(
            sink="memory_proposals",
            message=f"Memory proposal ingestion skipped invalid proposals: {failed}{suffix}.",
        )
    return None


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
    ranked: list[tuple[int, int, str]] = [
        (_SEVERITY_PRIORITY.get(signal.severity, 99), 0, signal.summary)
        for signal in detector_signals
    ] + [
        (_SEVERITY_PRIORITY.get(loop.severity, 99), 1, loop.description)
        for loop in read_models.open_loops.loops
    ]
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
            message=("Detector insight persistence failed; snapshot data may be fresher than stored history."),
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
    except Exception:  # Broad except is intentional: any failure must roll back the savepoint before re-raising
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
    except Exception:  # Broad except is intentional: any failure must roll back the savepoint before re-raising
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        conn.commit()


def _snapshot_json_default(value: object) -> object:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=lambda item: (str(type(item)), str(item)))
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _serialize_daily_snapshot_for_archive(snapshot: DailySnapshot) -> tuple[str, str]:
    """Serialize ``DailySnapshot`` to canonical JSON and SHA-256 hex digest of UTF-8 bytes."""
    payload = asdict(snapshot)
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_snapshot_json_default,
    )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, digest


def _execute_snapshot_archive_insert(
    conn: Connection,
    *,
    review_date: str,
    snapshot_json: str,
    content_hash: str,
) -> None:
    """Execute archive INSERT; separated for targeted tests (patching ``Connection.execute`` is not portable)."""
    conn.execute(
        """
        INSERT INTO snapshot_archives (
            review_date, generated_at, snapshot_json, content_hash, source
        ) VALUES (?, datetime('now'), ?, ?, ?)
        """,
        (review_date, snapshot_json, content_hash, "build_daily_snapshot"),
    )


def _persist_snapshot_archive(conn: Connection, review_date: str, snapshot: DailySnapshot) -> None:
    snapshot_json, content_hash = _serialize_daily_snapshot_for_archive(snapshot)
    try:
        _execute_snapshot_archive_insert(
            conn,
            review_date=review_date,
            snapshot_json=snapshot_json,
            content_hash=content_hash,
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        # The table enforces UNIQUE(review_date, content_hash); duplicates are
        # expected when ``build_daily_snapshot`` runs more than once in a day
        # without any observable state change (see test_snapshot.py::
        # _archive_count checks that re-running yields exactly one row).
        # That case is benign, but the original code swallowed the error with
        # zero telemetry, which made any *other* IntegrityError (e.g. a NOT
        # NULL violation introduced by a schema regression) indistinguishable
        # from the benign dedupe path. Emit an INFO record so operators can
        # see the skip happened and distinguish benign re-runs from real bugs.
        logger.info(
            "Snapshot archive already exists for %s (content_hash=%s); skipping insert: %s",
            review_date,
            content_hash,
            exc,
            extra={
                "domain": "core",
                "tool": "build_daily_snapshot",
                "success": True,
                "review_date": review_date,
                "content_hash": content_hash,
            },
        )
        with suppress(sqlite3.Error):
            conn.rollback()
    except Exception:
        logger.exception(
            "Snapshot archive persistence failed for %s",
            review_date,
            extra={
                "domain": "core",
                "tool": "build_daily_snapshot",
                "success": False,
            },
        )
        with suppress(sqlite3.Error):
            conn.rollback()


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
