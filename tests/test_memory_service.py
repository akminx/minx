from __future__ import annotations

import json

import pytest

from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.memory_models import MemoryProposal
from minx_mcp.core.memory_service import MemoryService
from minx_mcp.db import get_connection, migration_dir


def _fresh_memory_service(tmp_path) -> MemoryService:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    return MemoryService(db_path)


def test_create_memory_candidate_vs_active_and_event_trail(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    low = svc.create_memory(
        memory_type="t",
        scope="finance",
        subject="a",
        confidence=0.5,
        payload={"x": 1},
        source="src",
        reason="r",
        actor="user",
    )
    assert low.status == "candidate"
    high = svc.create_memory(
        memory_type="t",
        scope="finance",
        subject="b",
        confidence=0.85,
        payload={},
        source="src",
        actor="system",
    )
    assert high.status == "active"
    ev_low = svc.conn.execute(
        "SELECT event_type, actor FROM memory_events WHERE memory_id = ? ORDER BY id",
        (low.id,),
    ).fetchall()
    assert [tuple(r) for r in ev_low] == [("created", "user")]
    ev_high = svc.conn.execute(
        "SELECT event_type, actor FROM memory_events WHERE memory_id = ? ORDER BY id",
        (high.id,),
    ).fetchall()
    assert [tuple(r) for r in ev_high] == [("created", "system"), ("promoted", "system")]


def test_list_memories_no_default_status_filter(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    svc.create_memory(
        memory_type="t",
        scope="s",
        subject="cand",
        confidence=0.5,
        payload={},
        source="s",
        actor="user",
    )
    svc.create_memory(
        memory_type="t",
        scope="s",
        subject="act",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    all_rows = svc.list_memories()
    assert len(all_rows) == 2
    active_only = svc.list_memories(status="active")
    assert len(active_only) == 1
    assert active_only[0].subject == "act"


def test_get_memory_not_found(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    with pytest.raises(NotFoundError):
        svc.get_memory(404)


def test_confirm_reject_expire_and_payload_events(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="x",
        confidence=0.5,
        payload={"a": 1},
        source="s",
        actor="user",
    )
    confirmed = svc.confirm_memory(rec.id, actor="user")
    assert confirmed.status == "active"
    types = [
        r["event_type"]
        for r in svc.conn.execute(
            "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id",
            (rec.id,),
        ).fetchall()
    ]
    assert types == ["created", "confirmed"]

    rej = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="y",
        confidence=0.5,
        payload={},
        source="s",
        actor="user",
    )
    svc.reject_memory(rej.id, actor="user", reason="no")
    assert svc.get_memory(rej.id).status == "rejected"
    rej_types = [
        r["event_type"]
        for r in svc.conn.execute(
            "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id",
            (rej.id,),
        ).fetchall()
    ]
    assert rej_types[-1] == "rejected"

    exp = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="z",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    svc.expire_memory(exp.id, actor="system", reason="ttl")
    assert svc.get_memory(exp.id).status == "expired"

    upd = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="w",
        confidence=0.9,
        payload={"k": 1},
        source="s",
        actor="user",
    )
    svc.update_payload(upd.id, payload={"k": 2, "m": 3}, actor="harness")
    row = svc.get_memory(upd.id)
    assert row.payload == {"k": 2, "m": 3}
    last_ev = svc.conn.execute(
        "SELECT event_type, payload_json FROM memory_events WHERE memory_id = ? ORDER BY id DESC LIMIT 1",
        (upd.id,),
    ).fetchone()
    assert last_ev["event_type"] == "payload_updated"
    body = json.loads(str(last_ev["payload_json"]))
    assert body["payload"] == {"k": 2, "m": 3}


def test_list_pending_candidates_order(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    svc.create_memory(
        memory_type="t",
        scope="s",
        subject="low",
        confidence=0.2,
        payload={},
        source="s",
        actor="user",
    )
    svc.create_memory(
        memory_type="t",
        scope="s",
        subject="high",
        confidence=0.7,
        payload={},
        source="s",
        actor="user",
    )
    pending = svc.list_pending_candidates(limit=10)
    assert [p.subject for p in pending] == ["high", "low"]


def test_ingest_proposals_dedupe_merge_payload_and_promote(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    p1 = MemoryProposal(
        memory_type="recurring_merchant",
        scope="finance",
        subject="starbucks",
        confidence=0.5,
        payload={"cadence": "weekly"},
        source="detector:recurring_merchant",
        reason="first",
    )
    p2 = MemoryProposal(
        memory_type="recurring_merchant",
        scope="finance",
        subject="starbucks",
        confidence=0.85,
        payload={"typical_amount_cents": 500},
        source="detector:recurring_merchant",
        reason="second",
    )
    out = svc.ingest_proposals((p1, p2), actor="detector")
    assert len(out) == 2
    assert out[0].id == out[1].id
    final = svc.get_memory(out[0].id)
    assert final.confidence == 0.85
    assert final.status == "active"
    assert final.payload == {"cadence": "weekly", "typical_amount_cents": 500}
    assert final.reason == "second"
    ev = [
        (r["event_type"], r["actor"])
        for r in svc.conn.execute(
            "SELECT event_type, actor FROM memory_events WHERE memory_id = ? ORDER BY id",
            (final.id,),
        ).fetchall()
    ]
    assert ev[0] == ("created", "detector")
    assert ("payload_updated", "detector") in ev
    assert ("promoted", "detector") in ev
    assert ev.index(("payload_updated", "detector")) < ev.index(("promoted", "detector"))


def test_ingest_proposals_inserts_in_input_order_distinct_subjects(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    props = [
        MemoryProposal(
            memory_type="a",
            scope="s",
            subject="one",
            confidence=0.5,
            payload={},
            source="d",
            reason="",
        ),
        MemoryProposal(
            memory_type="a",
            scope="s",
            subject="two",
            confidence=0.5,
            payload={},
            source="d",
            reason="",
        ),
    ]
    out = svc.ingest_proposals(props, actor="detector")
    assert [r.subject for r in out] == ["one", "two"]
    assert out[0].id != out[1].id


def test_memory_events_cascade_on_delete_parent(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="cascade",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    mid = rec.id
    assert svc.conn.execute("SELECT COUNT(*) AS c FROM memory_events WHERE memory_id = ?", (mid,)).fetchone()["c"] >= 1
    svc.conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
    svc.conn.commit()
    left = svc.conn.execute(
        "SELECT COUNT(*) AS c FROM memory_events WHERE memory_id = ?",
        (mid,),
    ).fetchone()["c"]
    assert left == 0


def test_invalid_confidence_raises(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    with pytest.raises(InvalidInputError):
        svc.create_memory(
            memory_type="t",
            scope="s",
            subject="x",
            confidence=1.5,
            payload={},
            source="s",
            actor="user",
        )


def test_external_connection_path(tmp_path) -> None:
    db_path = tmp_path / "shared.db"
    conn = get_connection(db_path)
    try:
        svc = MemoryService(db_path, conn=conn)
        svc.create_memory(
            memory_type="t",
            scope="s",
            subject="ext",
            confidence=0.9,
            payload={},
            source="s",
            actor="user",
        )
        assert conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"] == 1
    finally:
        conn.close()


def test_update_payload_rejects_terminal_status(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="t",
        confidence=0.5,
        payload={},
        source="s",
        actor="user",
    )
    svc.reject_memory(rec.id, actor="user")
    with pytest.raises(InvalidInputError):
        svc.update_payload(rec.id, payload={"a": 1})


def test_confirm_non_candidate_raises(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="a",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    with pytest.raises(InvalidInputError):
        svc.confirm_memory(rec.id)


def test_migration_set_includes_014_snapshot_archives() -> None:
    names = sorted(p.name for p in migration_dir().glob("*.sql"))
    assert names[-1] == "014_slice6_snapshot_archives.sql"


def test_reject_memory_only_accepts_candidate(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    active = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="a",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    assert active.status == "active"
    with pytest.raises(InvalidInputError, match="Only candidate memories can be rejected"):
        svc.reject_memory(active.id, actor="user", reason="too late")
    still = svc.get_memory(active.id)
    assert still.status == "active"

    candidate = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="b",
        confidence=0.3,
        payload={},
        source="s",
        actor="user",
    )
    svc.reject_memory(candidate.id, actor="user", reason="not interested")
    with pytest.raises(InvalidInputError, match="Only candidate memories can be rejected"):
        svc.reject_memory(candidate.id, actor="user")

    expired = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="c",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    svc.expire_memory(expired.id, actor="system")
    with pytest.raises(InvalidInputError, match="Only candidate memories can be rejected"):
        svc.reject_memory(expired.id, actor="user")


def test_ingest_proposals_suppressed_by_prior_rejection(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    first = MemoryProposal(
        memory_type="recurring_merchant",
        scope="finance",
        subject="starbucks",
        confidence=0.4,
        payload={"cadence": "weekly"},
        source="detector:recurring_merchant",
        reason="first sight",
    )
    out1 = svc.ingest_proposals((first,), actor="detector")
    assert len(out1) == 1
    svc.reject_memory(out1[0].id, actor="user", reason="not recurring")

    again = MemoryProposal(
        memory_type="recurring_merchant",
        scope="finance",
        subject="starbucks",
        confidence=0.85,
        payload={"cadence": "weekly", "typical_amount_cents": 500},
        source="detector:recurring_merchant",
        reason="strong signal",
    )
    out2 = svc.ingest_proposals((again,), actor="detector")
    assert out2 == []
    rows = svc.conn.execute(
        """
        SELECT status, COUNT(*) AS c FROM memories
        WHERE memory_type = 'recurring_merchant'
          AND scope = 'finance'
          AND subject = 'starbucks'
        GROUP BY status
        """,
    ).fetchall()
    counts = {str(r["status"]): int(r["c"]) for r in rows}
    assert counts == {"rejected": 1}
    events = svc.conn.execute(
        "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id",
        (out1[0].id,),
    ).fetchall()
    event_types = [str(r["event_type"]) for r in events]
    assert event_types == ["created", "rejected"]


def test_ingest_proposals_mixed_with_and_without_rejection(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    a = MemoryProposal(
        memory_type="t",
        scope="s",
        subject="suppressed",
        confidence=0.5,
        payload={},
        source="d",
        reason="",
    )
    created = svc.ingest_proposals((a,), actor="detector")
    svc.reject_memory(created[0].id, actor="user")

    proposals = [
        MemoryProposal(
            memory_type="t",
            scope="s",
            subject="suppressed",
            confidence=0.9,
            payload={},
            source="d",
            reason="",
        ),
        MemoryProposal(
            memory_type="t",
            scope="s",
            subject="fresh",
            confidence=0.6,
            payload={},
            source="d",
            reason="",
        ),
    ]
    out = svc.ingest_proposals(proposals, actor="detector")
    assert [r.subject for r in out] == ["fresh"]


def test_ingest_proposals_after_expired_creates_new_row(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="cycled",
        confidence=0.9,
        payload={"v": 1},
        source="s",
        actor="user",
    )
    svc.expire_memory(rec.id, actor="system", reason="ttl")

    proposal = MemoryProposal(
        memory_type="t",
        scope="s",
        subject="cycled",
        confidence=0.85,
        payload={"v": 2},
        source="d",
        reason="new cycle",
    )
    out = svc.ingest_proposals((proposal,), actor="detector")
    assert len(out) == 1
    assert out[0].id != rec.id
    assert out[0].status == "active"
    assert out[0].payload == {"v": 2}
    rows = svc.conn.execute(
        "SELECT id, status FROM memories WHERE memory_type='t' AND scope='s' AND subject='cycled' ORDER BY id",
    ).fetchall()
    statuses = [(int(r["id"]), str(r["status"])) for r in rows]
    assert statuses == [(rec.id, "expired"), (out[0].id, "active")]
