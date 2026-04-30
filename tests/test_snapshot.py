from __future__ import annotations

import asyncio
import threading

import pytest

import minx_mcp.core.snapshot as snapshot_module
from minx_mcp.core.events import emit_event
from minx_mcp.core.goals import GoalService
from minx_mcp.core.memory_models import DetectorResult, MemoryProposal
from minx_mcp.core.memory_service import MemoryService
from minx_mcp.core.models import GoalCreateInput, InsightCandidate, SnapshotContext
from minx_mcp.core.snapshot import build_daily_snapshot
from minx_mcp.db import get_connection


def _archive_count(db_path) -> int:
    conn = get_connection(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) AS c FROM snapshot_archives").fetchone()["c"])
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_build_daily_snapshot_ingest_memories_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    proposal = MemoryProposal(
        memory_type="t",
        scope="s",
        subject="idempotent-subj",
        confidence=0.55,
        payload={"a": 1},
        source="detector:test",
        reason="r",
    )
    monkeypatch.setattr(
        snapshot_module,
        "_run_detectors",
        lambda _read_models: DetectorResult((), (proposal,)),
    )
    await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))
    await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))
    svc = MemoryService(db_path)
    try:
        rows = svc.conn.execute(
            "SELECT id FROM memories WHERE memory_type = 't' AND subject = 'idempotent-subj'"
        ).fetchall()
    finally:
        svc.close()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_build_daily_snapshot_identical_memory_proposals_do_not_churn_archive(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    proposal = MemoryProposal(
        memory_type="preference",
        scope="finance",
        subject="card-choice",
        confidence=0.55,
        payload={"value": "debit"},
        source="detector:test",
        reason="same evidence",
    )
    monkeypatch.setattr(
        snapshot_module,
        "_run_detectors",
        lambda _read_models: DetectorResult((), (proposal,)),
    )

    await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))
    await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))

    conn = get_connection(db_path)
    try:
        payload_updates = int(
            conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM memory_events
                WHERE event_type = 'payload_updated'
                """
            ).fetchone()["c"]
        )
    finally:
        conn.close()
    assert payload_updates == 0
    assert _archive_count(db_path) == 1


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
async def test_build_daily_snapshot_surfaces_memory_ingest_failures(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    bad = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="bad",
        confidence=0.9,
        payload={"not_a_field": "nope"},
        source="detector:test",
        reason="invalid",
    )
    monkeypatch.setattr(
        snapshot_module,
        "_run_detectors",
        lambda _read_models: DetectorResult((), (bad,)),
    )

    snapshot = await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))

    assert snapshot.persistence_warning is not None
    assert snapshot.persistence_warning.sink == "memory_proposals"
    assert "bad" in snapshot.persistence_warning.message


@pytest.mark.asyncio
async def test_build_daily_snapshot_emits_suppressed_info_log_alongside_failures_warning(
    tmp_path, monkeypatch, caplog
):
    """Snapshot emits BOTH an info-level suppressed log AND a warning
    persistence_warning on the same run (spec §10.4 L1170).
    """
    import logging

    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    # Seed a rejected row so that subsequent identical proposals are "suppressed"
    # (not counted as failures, but worth an info-level log).
    svc = MemoryService(db_path)
    try:
        rejectable = svc.create_memory(
            memory_type="preference",
            scope="core",
            subject="streaming",
            confidence=0.6,
            payload={"value": "old"},
            source="detector:test",
            reason="seed",
            actor="detector",
        )
        svc.reject_memory(rejectable.id, actor="user", reason="not-relevant")
    finally:
        svc.close()

    suppressed_proposal = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="streaming",
        confidence=0.6,
        payload={"value": "old"},
        source="detector:test",
        reason="re-proposed",
    )
    bad_proposal = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="bad-fail",
        confidence=0.9,
        payload={"not_a_field": "nope"},
        source="detector:test",
        reason="invalid",
    )
    monkeypatch.setattr(
        snapshot_module,
        "_run_detectors",
        lambda _read_models: DetectorResult((), (suppressed_proposal, bad_proposal)),
    )

    with caplog.at_level(logging.INFO, logger=snapshot_module.logger.name):
        snapshot = await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))

    # Failure path still surfaces as a persistence_warning.
    assert snapshot.persistence_warning is not None
    assert snapshot.persistence_warning.sink == "memory_proposals"
    assert "bad-fail" in snapshot.persistence_warning.message

    # Suppressed path still logs an info record (doesn't pester the user via
    # the warning channel, but must remain observable to operators).
    info_records = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO
        and "Memory proposal suppressed" in r.getMessage()
    ]
    assert info_records, (
        "suppressed rejected-prior proposals must still emit an info log "
        "even when a separate failure in the same batch drives the warning"
    )
    assert "streaming" in info_records[0].getMessage()


@pytest.mark.asyncio
async def test_build_daily_snapshot_refreshes_existing_detector_rows_on_rerun(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

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


@pytest.mark.asyncio
async def test_build_daily_snapshot_does_not_block_event_loop_under_gather(tmp_path, monkeypatch):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    original = snapshot_module._build_snapshot_models
    loop_thread_id = threading.get_ident()
    worker_entered = threading.Event()
    release_worker = threading.Event()
    worker_thread_ids: list[int] = []

    def slow_build(*args, **kwargs):
        worker_thread_ids.append(threading.get_ident())
        worker_entered.set()
        release_worker.wait(timeout=1)
        return original(*args, **kwargs)

    monkeypatch.setattr(snapshot_module, "_build_snapshot_models", slow_build)

    task = asyncio.create_task(build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path)))
    assert await asyncio.wait_for(asyncio.to_thread(worker_entered.wait), timeout=2)

    assert len(worker_thread_ids) == 1
    assert worker_thread_ids[0] != loop_thread_id, (
        "_build_snapshot_models ran on the event loop thread; "
        "snapshot work must stay in asyncio.to_thread"
    )
    release_worker.set()
    await task


@pytest.mark.asyncio
async def test_build_daily_snapshot_minimal_seeded_return_shape_unchanged(tmp_path):
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

    snapshot = await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))

    assert snapshot.date == "2026-03-15"
    assert snapshot.timeline.date == "2026-03-15"
    assert snapshot.spending.date == "2026-03-15"
    assert snapshot.open_loops.date == "2026-03-15"
    assert isinstance(snapshot.goal_progress, list)
    assert isinstance(snapshot.signals, list)
    assert isinstance(snapshot.attention_items, list)


@pytest.mark.asyncio
async def test_snapshot_continues_when_memory_ingest_raises(tmp_path, monkeypatch, caplog):
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

    def boom(self, proposals, *, actor="detector"):
        raise RuntimeError("boom")

    monkeypatch.setattr(snapshot_module.MemoryService, "ingest_proposals", boom)

    with caplog.at_level("WARNING", logger="minx_mcp.core.snapshot"):
        snapshot = await build_daily_snapshot("2026-03-15", SnapshotContext(db_path=db_path))

    assert snapshot.date == "2026-03-15"
    assert isinstance(snapshot.goal_progress, list)
    assert isinstance(snapshot.signals, list)
    assert snapshot.persistence_warning is not None
    assert snapshot.persistence_warning.sink == "memory_proposals"
    assert any(
        "Memory proposal ingestion failed" in r.getMessage() and "boom" in r.getMessage()
        for r in caplog.records
    )
    assert _archive_count(db_path) == 1
