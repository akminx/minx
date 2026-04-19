from __future__ import annotations

import pytest

from minx_mcp.core.memory_service import MemoryService
from minx_mcp.core.models import MemoryContextItem, SnapshotContext
from minx_mcp.core.snapshot import build_daily_snapshot
from minx_mcp.db import get_connection


@pytest.mark.asyncio
async def test_snapshot_memory_context_no_memories(tmp_path):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()

    snapshot = await build_daily_snapshot("2026-04-18", SnapshotContext(db_path=db_path))

    assert snapshot.memory_context is not None
    assert snapshot.memory_context.active == []
    assert snapshot.memory_context.pending_candidate_count == 0
    assert snapshot.memory_context.recent_events == []


@pytest.mark.asyncio
async def test_snapshot_memory_context_with_active_and_candidate_memories(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)

    # Insert one active memory (confidence >= 0.8)
    svc.create_memory(
        memory_type="preference",
        scope="finance",
        subject="prefers-debit",
        confidence=0.9,
        payload={"value": "debit"},
        source="test",
        actor="user",
    )
    # Insert one candidate memory (confidence < 0.8)
    svc.create_memory(
        memory_type="preference",
        scope="fitness",
        subject="morning-workouts",
        confidence=0.6,
        payload={"value": "morning"},
        source="test",
        actor="user",
    )
    conn.commit()
    conn.close()

    snapshot = await build_daily_snapshot("2026-04-18", SnapshotContext(db_path=db_path))

    assert snapshot.memory_context is not None
    assert len(snapshot.memory_context.active) == 1
    assert isinstance(snapshot.memory_context.active[0], MemoryContextItem)
    assert snapshot.memory_context.active[0].subject == "prefers-debit"
    assert snapshot.memory_context.active[0].payload == {"value": "debit"}
    assert snapshot.memory_context.pending_candidate_count == 1
    assert snapshot.memory_context.recent_events
    assert "1 memory candidates need review." in snapshot.attention_items


@pytest.mark.asyncio
async def test_snapshot_memory_context_active_count_matches_active_memories_len(tmp_path):
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    svc = MemoryService(db_path, conn=conn)

    for i in range(3):
        svc.create_memory(
            memory_type="preference",
            scope="finance",
            subject=f"pref-{i}",
            confidence=0.95,
            payload={"value": str(i)},
            source="test",
            actor="user",
        )
    conn.commit()
    conn.close()

    snapshot = await build_daily_snapshot("2026-04-18", SnapshotContext(db_path=db_path))

    assert snapshot.memory_context is not None
    assert len(snapshot.memory_context.active) == 3
