from __future__ import annotations

import pytest

from minx_mcp.core.events import emit_event
from minx_mcp.core.goals import GoalService
from minx_mcp.core.memory_models import DetectorResult
from minx_mcp.core.models import GoalCreateInput, InsightCandidate, SnapshotContext
from minx_mcp.core.snapshot import build_daily_snapshot
from minx_mcp.db import get_connection


@pytest.mark.asyncio
async def test_build_daily_snapshot_returns_structured_data_and_persists_detector_signals(tmp_path):
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

    snapshot = await build_daily_snapshot(
        "2026-03-15",
        SnapshotContext(db_path=db_path),
    )

    assert snapshot.date == "2026-03-15"
    assert snapshot.timeline.entries[0].event_type == "finance.transactions_imported"
    assert snapshot.goal_progress[0].status == "off_track"
    assert any(item.endswith("is off track.") for item in snapshot.attention_items)
    assert snapshot.persistence_warning is None

    persisted = _read_persisted_insights(db_path)
    assert any(row["insight_type"] == "core.goal_drift" for row in persisted)


@pytest.mark.asyncio
async def test_build_daily_snapshot_returns_persistence_warning_when_detector_write_fails(
    tmp_path,
    monkeypatch,
):
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

    import minx_mcp.core.snapshot as snapshot_module

    monkeypatch.setattr(
        snapshot_module,
        "_insert_detector_insights",
        lambda conn, review_date, insights: (_ for _ in ()).throw(RuntimeError("db boom")),
    )

    snapshot = await build_daily_snapshot(
        "2026-03-15",
        SnapshotContext(db_path=db_path),
    )

    assert snapshot.persistence_warning is not None
    assert snapshot.persistence_warning.sink == "detector_insights"
    assert _read_persisted_insights(db_path) == []


@pytest.mark.asyncio
async def test_build_daily_snapshot_refreshes_existing_detector_rows_on_rerun(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    import minx_mcp.core.snapshot as snapshot_module

    monkeypatch.setattr(
        snapshot_module,
        "_run_detectors",
        lambda _read_models: DetectorResult.insights_only(
            InsightCandidate(
                insight_type="finance.spending_spike",
                dedupe_key="2026-03-15:spending_spike:dining-out",
                summary="First summary",
                supporting_signals=["first"],
                confidence=0.6,
                severity="warning",
                actionability="suggestion",
                source="detector",
            )
        ),
    )
    await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))

    monkeypatch.setattr(
        snapshot_module,
        "_run_detectors",
        lambda _read_models: DetectorResult.insights_only(
            InsightCandidate(
                insight_type="finance.spending_spike",
                dedupe_key="2026-03-15:spending_spike:dining-out",
                summary="Updated summary",
                supporting_signals=["updated"],
                confidence=0.9,
                severity="alert",
                actionability="action",
                source="detector",
            )
        ),
    )
    await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT summary, supporting_signals, confidence, severity, actionability
            FROM insights
            WHERE review_date = '2026-03-15' AND dedupe_key = '2026-03-15:spending_spike:dining-out'
            """
        ).fetchone()
    finally:
        conn.close()

    assert row["summary"] == "Updated summary"
    assert row["supporting_signals"] == '["updated"]'
    assert row["confidence"] == 0.9
    assert row["severity"] == "alert"
    assert row["actionability"] == "action"


def _seed_goal(conn) -> None:
    GoalService(conn).create_goal(
        GoalCreateInput(
            goal_type="spending_cap",
            title="Dining cap",
            metric_type="sum_below",
            target_value=5000,
            period="monthly",
            domain="finance",
            category_names=["Dining Out"],
            merchant_names=[],
            account_names=[],
            starts_on="2026-03-01",
            ends_on=None,
            notes=None,
        )
    )


def _seed_transaction(
    conn, *, posted_at: str, merchant: str, amount_cents: int, category_name: str
) -> None:
    category_id = conn.execute(
        "SELECT id FROM finance_categories WHERE name = ?",
        (category_name,),
    ).fetchone()["id"]
    account_id = conn.execute("SELECT id FROM finance_accounts WHERE name = 'DCU'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, ?, 'csv', 'seed.csv', 'fp')
        """,
        (account_id,),
    )
    conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, 1, ?, ?, ?, ?, ?, 'manual')
        """,
        (account_id, posted_at, merchant, merchant, amount_cents, category_id),
    )


def _read_persisted_insights(db_path):
    conn = get_connection(db_path)
    try:
        return conn.execute(
            "SELECT insight_type, source FROM insights ORDER BY insight_type"
        ).fetchall()
    finally:
        conn.close()
