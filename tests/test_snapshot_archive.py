from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict

import pytest

import minx_mcp.core.snapshot as snapshot_module
from minx_mcp.core.events import emit_event
from minx_mcp.core.goal_models import GoalProgress
from minx_mcp.core.models import (
    DailySnapshot,
    DailyTimeline,
    InsightCandidate,
    NutritionSnapshot,
    OpenLoopsSnapshot,
    SnapshotContext,
    SpendingSnapshot,
    TimelineEntry,
    TrainingSnapshot,
)
from minx_mcp.core.snapshot import (
    _serialize_daily_snapshot_for_archive,
    build_daily_snapshot,
)
from minx_mcp.db import get_connection
from tests.test_snapshot import _seed_goal, _seed_transaction


def _canonicalize(obj: object) -> object:
    if isinstance(obj, dict):
        return {str(k): _canonicalize(obj[k]) for k in sorted(obj, key=lambda x: str(x))}
    if isinstance(obj, list):
        return [_canonicalize(x) for x in obj]
    if isinstance(obj, tuple):
        return [_canonicalize(x) for x in obj]
    return obj


def _archive_count(db_path) -> int:
    conn = get_connection(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) AS c FROM snapshot_archives").fetchone()["c"])
    finally:
        conn.close()


def test_snapshot_archives_table_exists_empty_after_migrations(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    try:
        names = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "snapshot_archives" in names
        count = int(conn.execute("SELECT COUNT(*) AS c FROM snapshot_archives").fetchone()["c"])
        assert count == 0
    finally:
        conn.close()


def test_daily_snapshot_serializer_round_trip() -> None:
    snap = DailySnapshot(
        date="2026-04-17",
        timeline=DailyTimeline(
            date="2026-04-17",
            entries=[
                TimelineEntry(
                    occurred_at="2026-04-17T12:00:00Z",
                    domain="finance",
                    event_type="finance.transactions_imported",
                    summary="Imported",
                    entity_ref="batch:1",
                )
            ],
        ),
        spending=SpendingSnapshot(
            date="2026-04-17",
            total_spent_cents=100,
            by_category={"dining": 50},
            top_merchants=[("Cafe", -50)],
            vs_prior_week_pct=None,
            uncategorized_count=0,
            uncategorized_total_cents=0,
        ),
        open_loops=OpenLoopsSnapshot(date="2026-04-17", loops=[]),
        goal_progress=[
            GoalProgress(
                goal_id=1,
                title="cap",
                metric_type="sum_below",
                target_value=5000,
                actual_value=6000,
                remaining_value=None,
                current_start="2026-04-01",
                current_end="2026-04-30",
                status="off_track",
                summary="over",
                category_names=["Dining Out"],
                merchant_names=[],
                account_names=[],
            )
        ],
        signals=[
            InsightCandidate(
                insight_type="test.signal",
                dedupe_key="k",
                summary="s",
                supporting_signals=["a"],
                confidence=0.5,
                severity="info",
                actionability="suggestion",
                source="detector",
            )
        ],
        attention_items=["watch"],
        nutrition=NutritionSnapshot(
            date="2026-04-17",
            meal_count=1,
            protein_grams=10.5,
            calories=200,
            last_meal_at=None,
            skipped_meal_signals=[],
        ),
        training=TrainingSnapshot(
            date="2026-04-17",
            sessions_logged=0,
            total_sets=0,
            total_volume_kg=0.0,
            last_session_at=None,
            adherence_signal="none",
        ),
        persistence_warning=None,
    )
    text, digest = _serialize_daily_snapshot_for_archive(snap)
    loaded = json.loads(text)
    assert _canonicalize(loaded) == _canonicalize(asdict(snap))
    assert digest == hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_build_daily_snapshot_archives_dedupe_and_tracks_content_change(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_goal(conn)
    _seed_transaction(
        conn,
        posted_at="2026-03-15",
        merchant="Cafe",
        amount_cents=-6800,
        category_name="Dining Out",
    )
    emit_event(
        conn,
        event_type="finance.transactions_imported",
        domain="finance",
        occurred_at="2026-03-15T12:00:00Z",
        entity_ref="job:1",
        source="tests",
        payload={
            "account_name": "DCU",
            "account_id": 1,
            "job_id": "job-1",
            "transaction_count": 1,
            "total_cents": -6800,
            "source_kind": "csv",
        },
    )
    conn.commit()
    conn.close()

    await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))
    assert _archive_count(db_path) == 1

    await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))
    assert _archive_count(db_path) == 1

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT snapshot_json, content_hash FROM snapshot_archives WHERE id = 1"
    ).fetchone()
    assert row is not None
    assert hashlib.sha256(row["snapshot_json"].encode("utf-8")).hexdigest() == row["content_hash"]
    first_hash = row["content_hash"]
    conn.close()

    conn = get_connection(db_path)
    conn.execute(
        """
        UPDATE finance_transactions
        SET amount_cents = ?
        WHERE posted_at = ? AND merchant = ?
        """,
        (-9999, "2026-03-15", "Cafe"),
    )
    conn.commit()
    conn.close()

    await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))
    assert _archive_count(db_path) == 2
    conn = get_connection(db_path)
    hashes = {
        r["content_hash"]
        for r in conn.execute("SELECT content_hash FROM snapshot_archives").fetchall()
    }
    conn.close()
    assert len(hashes) == 2
    assert first_hash in hashes


@pytest.mark.asyncio
async def test_archive_insert_operational_error_does_not_abort_snapshot(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    def boom(
        conn: sqlite3.Connection,
        *,
        review_date: str,
        snapshot_json: str,
        content_hash: str,
    ) -> None:
        raise sqlite3.OperationalError("simulated failure")

    monkeypatch.setattr(snapshot_module, "_execute_snapshot_archive_insert", boom)

    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    _seed_goal(conn)
    _seed_transaction(
        conn,
        posted_at="2026-03-15",
        merchant="Cafe",
        amount_cents=-6800,
        category_name="Dining Out",
    )
    conn.commit()
    conn.close()

    with caplog.at_level("ERROR", logger="minx_mcp.core.snapshot"):
        snapshot = await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))

    assert snapshot.date == "2026-03-15"
    assert _archive_count(db_path) == 0
    assert any(
        "Snapshot archive persistence failed" in r.getMessage() for r in caplog.records
    )
