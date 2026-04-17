from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from minx_mcp.contracts import ConflictError, InvalidInputError, NotFoundError
from minx_mcp.core.memory_models import MemoryProposal
from minx_mcp.core.memory_service import MemoryService
from minx_mcp.db import get_connection, migration_dir


def _fresh_memory_service(tmp_path) -> MemoryService:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    return MemoryService(db_path)


def _proxy_conn_first_memory_id_select(
    inner: sqlite3.Connection,
    *,
    memory_id: int,
    between: Callable[[], None],
) -> sqlite3.Connection:
    """Return a connection proxy that runs ``between`` after the first ``fetchone`` on id-lookup SELECT."""

    class _Proxy:
        __slots__ = ("_inner",)

        def __init__(self, c: sqlite3.Connection) -> None:
            self._inner = c

        def execute(self, sql: str, parameters: Any = ()) -> Any:  # noqa: ANN401
            cur = self._inner.execute(sql, parameters)
            if (
                isinstance(sql, str)
                and "SELECT * FROM memories WHERE id = ?" in sql.strip()
                and tuple(parameters) == (memory_id,)
            ):

                class _Cursor:
                    def __init__(self, base: Any) -> None:  # noqa: ANN401
                        self._base = base
                        self._first_fetch = True

                    def fetchone(self) -> Any:  # noqa: ANN401
                        if self._first_fetch:
                            self._first_fetch = False
                            row = self._base.fetchone()
                            between()
                            return row
                        return self._base.fetchone()

                    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
                        return getattr(self._base, name)

                return _Cursor(cur)
            return cur

        def __getattr__(self, name: str) -> Any:  # noqa: ANN401
            return getattr(self._inner, name)

    return _Proxy(inner)  # type: ignore[return-value]


def _proxy_conn_first_prior_row_fetch(
    inner: sqlite3.Connection,
    *,
    memory_type: str,
    scope: str,
    subject: str,
    between: Callable[[], None],
) -> sqlite3.Connection:
    """Proxy that runs ``between`` after the first ingest prior-row ``fetchone`` for a triple."""

    class _Proxy:
        __slots__ = ("_inner",)

        def __init__(self, c: sqlite3.Connection) -> None:
            self._inner = c

        def execute(self, sql: str, parameters: Any = ()) -> Any:  # noqa: ANN401
            cur = self._inner.execute(sql, parameters)
            if (
                isinstance(sql, str)
                and "FROM memories" in sql
                and "ORDER BY updated_at DESC, id DESC" in sql
                and "LIMIT 1" in sql
                and tuple(parameters) == (memory_type, scope, subject)
            ):

                class _Cursor:
                    def __init__(self, base: Any) -> None:  # noqa: ANN401
                        self._base = base
                        self._first_fetch = True

                    def fetchone(self) -> Any:  # noqa: ANN401
                        if self._first_fetch:
                            self._first_fetch = False
                            row = self._base.fetchone()
                            between()
                            return row
                        return self._base.fetchone()

                    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
                        return getattr(self._base, name)

                return _Cursor(cur)
            return cur

        def __getattr__(self, name: str) -> Any:  # noqa: ANN401
            return getattr(self._inner, name)

    return _Proxy(inner)  # type: ignore[return-value]


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


def test_ingest_proposals_merge_drops_legacy_unknown_keys_on_canonical_type(tmp_path) -> None:
    """Legacy rows from pre-schema Slice 6a testing may have unknown keys in
    payload_json; on merge against a canonical (pydantic-validated) type,
    those keys must drop out rather than compounding forever.
    """
    svc = _fresh_memory_service(tmp_path)
    # Seed a preference row directly with legacy junk (bypassing the public
    # create_memory path so we don't hit validation on insert — this is the
    # shape real pre-Slice-6-review rows already have).
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status,
            payload_json, source, reason, created_at, updated_at
        )
        VALUES ('preference', 'core', 'pizza', 0.5, 'candidate',
                ?, 'manual:seed', 'legacy',
                datetime('now'), datetime('now'))
        """,
        (json.dumps({"note": "old", "legacy_key": "should_drop", "stale": 42}),),
    )
    svc.conn.commit()

    # Now ingest a proposal that targets the same (type, scope, subject) with
    # a valid payload; merge should drop legacy_key and stale.
    p = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="pizza",
        confidence=0.6,
        payload={"category": "food"},
        source="detector:test",
        reason="merge-test",
    )
    out = svc.ingest_proposals((p,), actor="detector")
    final = svc.get_memory(out[0].id)
    assert final.payload == {"note": "old", "category": "food"}
    assert "legacy_key" not in final.payload
    assert "stale" not in final.payload


def test_ingest_proposals_merge_keeps_valid_prior_subset(tmp_path) -> None:
    """Valid prior fields are retained on merge; only junk gets dropped."""
    svc = _fresh_memory_service(tmp_path)
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status,
            payload_json, source, reason, created_at, updated_at
        )
        VALUES ('preference', 'core', 'coffee', 0.5, 'candidate',
                ?, 'manual:seed', 'legacy',
                datetime('now'), datetime('now'))
        """,
        (json.dumps({"note": "keep", "category": "food"}),),
    )
    svc.conn.commit()
    p = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.6,
        payload={"value": "decaf"},
        source="detector:test",
        reason="merge-test",
    )
    out = svc.ingest_proposals((p,), actor="detector")
    final = svc.get_memory(out[0].id)
    assert final.payload == {"note": "keep", "category": "food", "value": "decaf"}


def test_create_memory_empty_payload_stays_sparse(tmp_path) -> None:
    """Regression for f2: model_dump used to expand {} into full-null dict."""
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="test",
        confidence=0.6,
        payload={},
        source="manual:user",
    )
    assert rec.payload == {}


def test_create_memory_single_field_stays_sparse(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="test",
        confidence=0.6,
        payload={"note": "hello"},
        source="manual:user",
    )
    assert rec.payload == {"note": "hello"}
    assert "category" not in rec.payload
    assert "value" not in rec.payload


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


def test_migration_set_includes_015_memories_unique_live() -> None:
    names = sorted(p.name for p in migration_dir().glob("*.sql"))
    assert "015_slice6_memories_unique_live.sql" in names
    assert "016_memory_ttl_and_event_check.sql" in names
    assert "017_recipes_vault_synced_at.sql" in names
    assert names[-1] == "017_recipes_vault_synced_at.sql"


def test_unique_index_rejects_duplicate_live_triple(tmp_path) -> None:
    import sqlite3

    svc = _fresh_memory_service(tmp_path)
    svc.create_memory(
        memory_type="recurring_merchant",
        scope="finance",
        subject="starbucks",
        confidence=0.9,
        payload={"cadence": "weekly"},
        source="s",
        actor="user",
    )
    with pytest.raises(sqlite3.IntegrityError):
        svc.conn.execute(
            """
            INSERT INTO memories (
                memory_type, scope, subject, confidence, status,
                payload_json, source, reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                "recurring_merchant",
                "finance",
                "starbucks",
                0.4,
                "candidate",
                "{}",
                "s",
                "",
            ),
        )
    svc.conn.rollback()


def test_unique_index_allows_rejected_plus_live_after_lifecycle(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="cycled",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    svc.expire_memory(rec.id, actor="system")
    fresh = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="cycled",
        confidence=0.5,
        payload={},
        source="s",
        actor="user",
    )
    assert fresh.id != rec.id
    statuses = [
        (int(r["id"]), str(r["status"]))
        for r in svc.conn.execute(
            "SELECT id, status FROM memories WHERE subject='cycled' ORDER BY id",
        ).fetchall()
    ]
    assert statuses == [(rec.id, "expired"), (fresh.id, "candidate")]


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


def test_list_pending_candidates_scope_filter(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    svc.create_memory(
        memory_type="t",
        scope="finance",
        subject="fin_a",
        confidence=0.5,
        payload={},
        source="s",
        actor="user",
    )
    svc.create_memory(
        memory_type="t",
        scope="meals",
        subject="meal_a",
        confidence=0.4,
        payload={},
        source="s",
        actor="user",
    )
    svc.create_memory(
        memory_type="t",
        scope="finance",
        subject="fin_b",
        confidence=0.3,
        payload={},
        source="s",
        actor="user",
    )
    all_candidates = svc.list_pending_candidates()
    assert {c.subject for c in all_candidates} == {"fin_a", "fin_b", "meal_a"}
    finance_only = svc.list_pending_candidates(scope="finance")
    assert {c.subject for c in finance_only} == {"fin_a", "fin_b"}
    meals_only = svc.list_pending_candidates(scope="meals")
    assert [c.subject for c in meals_only] == ["meal_a"]
    with pytest.raises(InvalidInputError):
        svc.list_pending_candidates(scope="  ")


def test_list_memories_scope_filter(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    svc.create_memory(
        memory_type="t",
        scope="finance",
        subject="a",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    svc.create_memory(
        memory_type="t",
        scope="meals",
        subject="b",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    svc.create_memory(
        memory_type="t",
        scope="meals",
        subject="c",
        confidence=0.3,
        payload={},
        source="s",
        actor="user",
    )
    finance = svc.list_memories(scope="finance")
    assert [m.subject for m in finance] == ["a"]
    meals_all = svc.list_memories(scope="meals")
    assert {m.subject for m in meals_all} == {"b", "c"}
    meals_active = svc.list_memories(scope="meals", status="active")
    assert [m.subject for m in meals_active] == ["b"]


def test_expire_memory_rejects_non_active_statuses(tmp_path) -> None:
    """Expire is the active→expired path only.

    This is the correctness rail that prevents ``ingest_proposals`` from
    resurrecting a rejected memory (rejected→expired→"looks like a TTL expiry"
    → detector re-proposes and inserts a fresh candidate).
    """
    svc = _fresh_memory_service(tmp_path)

    candidate = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="cand",
        confidence=0.3,
        payload={},
        source="s",
        actor="user",
    )
    assert candidate.status == "candidate"
    with pytest.raises(InvalidInputError, match="Only active memories can be expired"):
        svc.expire_memory(candidate.id, actor="system")
    assert svc.get_memory(candidate.id).status == "candidate"

    rejected = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="rej",
        confidence=0.3,
        payload={},
        source="s",
        actor="user",
    )
    svc.reject_memory(rejected.id, actor="user", reason="not interested")
    with pytest.raises(InvalidInputError, match="Only active memories can be expired"):
        svc.expire_memory(rejected.id, actor="system")
    assert svc.get_memory(rejected.id).status == "rejected"

    active = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="act",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    assert active.status == "active"
    expired = svc.expire_memory(active.id, actor="system")
    assert expired.status == "expired"
    again = svc.expire_memory(active.id, actor="system")
    assert again.status == "expired"
    events = svc.conn.execute(
        "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id",
        (active.id,),
    ).fetchall()
    expired_events = [e for e in events if str(e["event_type"]) == "expired"]
    assert len(expired_events) == 1, (
        "expire_memory must be idempotent: a second call on an already-expired "
        "row must not emit a second 'expired' event"
    )


def test_rejected_prior_stays_sticky_even_after_expire_attempts(tmp_path) -> None:
    """Regression guard for the reject→expire→re-ingest resurrection bug.

    Before the expire_memory active-only guard, a detector could effectively
    resurrect a rejected memory by (1) having the row end up in 'expired' state
    somehow, (2) re-proposing it, because ingest_proposals treats expired-prior
    as a fresh lifecycle. The guard in expire_memory plus the suppression path
    in ingest_proposals together make rejection truly terminal.
    """
    svc = _fresh_memory_service(tmp_path)
    candidate = svc.create_memory(
        memory_type="recurring_merchant",
        scope="finance",
        subject="starbucks",
        confidence=0.4,
        payload={"cadence": "weekly"},
        source="detector:recurring_merchant",
        actor="detector",
    )
    svc.reject_memory(candidate.id, actor="user", reason="I don't want this tracked")

    with pytest.raises(InvalidInputError):
        svc.expire_memory(candidate.id, actor="system")

    proposal = MemoryProposal(
        memory_type="recurring_merchant",
        scope="finance",
        subject="starbucks",
        confidence=0.9,
        payload={"cadence": "weekly"},
        source="detector:recurring_merchant",
        reason="resurgence",
    )
    out = svc.ingest_proposals((proposal,), actor="detector")
    assert out == [], "rejected-prior must remain sticky across re-ingest"
    rows = svc.conn.execute(
        "SELECT id, status FROM memories "
        "WHERE memory_type='recurring_merchant' AND scope='finance' "
        "AND subject='starbucks' ORDER BY id"
    ).fetchall()
    assert [(int(r["id"]), str(r["status"])) for r in rows] == [
        (candidate.id, "rejected"),
    ]


def test_create_memory_duplicate_live_triple_raises_conflict(tmp_path) -> None:
    """Migration 015's partial unique index surfaces as a CONFLICT error.

    Without explicit mapping, ``sqlite3.IntegrityError`` would surface as a
    generic INTERNAL_ERROR to MCP clients — obscuring an actionable operator
    mistake (trying to manually create a memory that already has a live row).
    """
    from minx_mcp.contracts import ConflictError

    svc = _fresh_memory_service(tmp_path)
    svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="tz",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="user",
        actor="user",
    )
    with pytest.raises(ConflictError) as excinfo:
        svc.create_memory(
            memory_type="preference",
            scope="core",
            subject="tz",
            confidence=0.4,
            payload={"category": "timezone", "value": "America/Los_Angeles"},
            source="user",
            actor="user",
        )
    assert excinfo.value.data == {
        "memory_type": "preference",
        "scope": "core",
        "subject": "tz",
    }


def test_create_memory_conflict_detection_ignores_unrelated_integrity_errors(tmp_path) -> None:
    """Non-live IntegrityErrors must NOT be remapped as CONFLICT.

    The detection strategy is state-based (look for a live row for the
    proposed triple) rather than parsing SQLite's error message. That makes
    the mapping robust to future DDL that might add other UNIQUE or CHECK
    constraints whose failure messages could superficially resemble the
    live-triple index. Simulate such a case with a trigger that raises
    IntegrityError for a marker subject — no live row exists for that
    triple, so the error must propagate unchanged, not be dressed up as a
    user-addressable CONFLICT.
    """
    from sqlite3 import IntegrityError

    svc = _fresh_memory_service(tmp_path)
    svc.conn.execute(
        """
        CREATE TRIGGER _force_integrity_error_for_test
        BEFORE INSERT ON memories
        WHEN NEW.subject = 'trigger-tripwire'
        BEGIN
            SELECT RAISE(ABORT, 'synthetic integrity error for testing');
        END
        """
    )
    svc.conn.commit()

    with pytest.raises(IntegrityError, match="synthetic integrity error"):
        svc.create_memory(
            memory_type="preference",
            scope="core",
            subject="trigger-tripwire",
            confidence=0.9,
            payload={},
            source="user",
            actor="user",
        )


def test_list_memories_rejects_unknown_status(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    with pytest.raises(InvalidInputError, match="status must be one of"):
        svc.list_memories(status="nope")


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


def test_confirm_race_does_not_overwrite_rejection(tmp_path) -> None:
    db_path = tmp_path / "race.db"
    get_connection(db_path).close()
    svc_a = MemoryService(db_path)
    rec = svc_a.create_memory(
        memory_type="t",
        scope="s",
        subject="race_confirm",
        confidence=0.4,
        payload={"k": 1},
        source="s",
        actor="user",
    )
    conn_b = get_connection(db_path)
    try:
        svc_b = MemoryService(
            db_path,
            conn=_proxy_conn_first_memory_id_select(
                conn_b,
                memory_id=rec.id,
                between=lambda: svc_a.reject_memory(rec.id, actor="user", reason="no"),
            ),
        )
        with pytest.raises(ConflictError) as excinfo:
            svc_b.confirm_memory(rec.id, actor="user")
        assert excinfo.value.data == {"memory_id": rec.id, "expected_status": "candidate"}
    finally:
        conn_b.close()
    final = svc_a.get_memory(rec.id)
    assert final.status == "rejected"
    assert final.payload == {"k": 1}


def test_reject_memory_pre_read_rejects_non_candidate_sequentially(tmp_path) -> None:
    """Sequential pre-check coverage: reject on an already-active row raises InvalidInputError.

    Not a race test — both services observe the post-confirm state. For the actual
    race (both see candidate, one commits between the other's pre-read and UPDATE),
    see ``test_reject_memory_race_raises_conflict_after_concurrent_confirm``.
    """
    db_path = tmp_path / "race_reject.db"
    get_connection(db_path).close()
    svc_a = MemoryService(db_path)
    svc_b = MemoryService(db_path)
    rec = svc_a.create_memory(
        memory_type="t",
        scope="s",
        subject="race_reject",
        confidence=0.4,
        payload={},
        source="s",
        actor="user",
    )
    svc_a.confirm_memory(rec.id, actor="user")
    with pytest.raises(InvalidInputError, match="Only candidate memories can be rejected"):
        svc_b.reject_memory(rec.id, actor="user", reason="late")
    assert svc_b.get_memory(rec.id).status == "active"


def test_reject_memory_race_raises_conflict_after_concurrent_confirm(tmp_path) -> None:
    """Real race: svc_b pre-reads candidate, svc_a confirms, svc_b's guarded UPDATE misses.

    Without the ``AND status = ?`` guard on the UPDATE, svc_b would flip an already-active
    memory to rejected and log a spurious 'rejected' event. With the guard, rowcount is 0
    and svc_b raises ConflictError with expected_status='candidate', leaving the row active.
    """
    db_path = tmp_path / "race_reject_real.db"
    get_connection(db_path).close()
    svc_a = MemoryService(db_path)
    rec = svc_a.create_memory(
        memory_type="t",
        scope="s",
        subject="race_reject_real",
        confidence=0.4,
        payload={"k": 1},
        source="s",
        actor="user",
    )
    def _confirm_between() -> None:
        svc_a.confirm_memory(rec.id, actor="user")

    conn_b = get_connection(db_path)
    try:
        svc_b = MemoryService(
            db_path,
            conn=_proxy_conn_first_memory_id_select(
                conn_b,
                memory_id=rec.id,
                between=_confirm_between,
            ),
        )
        with pytest.raises(ConflictError) as excinfo:
            svc_b.reject_memory(rec.id, actor="user", reason="late")
        assert excinfo.value.data == {"memory_id": rec.id, "expected_status": "candidate"}
    finally:
        conn_b.close()
    final = svc_a.get_memory(rec.id)
    assert final.status == "active"
    assert final.payload == {"k": 1}
    event_types = [
        str(r["event_type"])
        for r in svc_a.conn.execute(
            "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id",
            (rec.id,),
        ).fetchall()
    ]
    assert event_types == ["created", "confirmed"]


def test_expire_race_does_not_overwrite_rejected(tmp_path) -> None:
    db_path = tmp_path / "race_expire.db"
    get_connection(db_path).close()
    svc_a = MemoryService(db_path)
    rec = svc_a.create_memory(
        memory_type="t",
        scope="s",
        subject="race_expire",
        confidence=0.9,
        payload={"v": 1},
        source="s",
        actor="user",
    )
    assert rec.status == "active"
    conn_b = get_connection(db_path)
    try:
        svc_b = MemoryService(
            db_path,
            conn=_proxy_conn_first_memory_id_select(
                conn_b,
                memory_id=rec.id,
                between=lambda: svc_a.expire_memory(rec.id, actor="system", reason="ttl"),
            ),
        )
        with pytest.raises(ConflictError) as excinfo:
            svc_b.expire_memory(rec.id, actor="system", reason="late")
        assert excinfo.value.data == {"memory_id": rec.id, "expected_status": "active"}
    finally:
        conn_b.close()
    assert svc_a.get_memory(rec.id).status == "expired"
    assert svc_a.get_memory(rec.id).payload == {"v": 1}


def test_update_payload_race_does_not_write_to_rejected(tmp_path) -> None:
    db_path = tmp_path / "race_payload.db"
    get_connection(db_path).close()
    svc_a = MemoryService(db_path)
    rec = svc_a.create_memory(
        memory_type="t",
        scope="s",
        subject="race_payload",
        confidence=0.5,
        payload={"orig": 1},
        source="s",
        actor="user",
    )
    conn_b = get_connection(db_path)
    try:
        svc_b = MemoryService(
            db_path,
            conn=_proxy_conn_first_memory_id_select(
                conn_b,
                memory_id=rec.id,
                between=lambda: svc_a.reject_memory(rec.id, actor="user", reason="no"),
            ),
        )
        with pytest.raises(ConflictError) as excinfo:
            svc_b.update_payload(rec.id, payload={"orig": 9, "new": 2}, actor="harness")
        assert excinfo.value.data == {"memory_id": rec.id, "expected_status": "candidate"}
    finally:
        conn_b.close()
    assert svc_a.get_memory(rec.id).status == "rejected"
    assert svc_a.get_memory(rec.id).payload == {"orig": 1}
    last_ev = svc_a.conn.execute(
        "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id DESC LIMIT 1",
        (rec.id,),
    ).fetchone()
    assert str(last_ev["event_type"]) == "rejected"


def test_ingest_merge_does_not_overwrite_rejected(tmp_path) -> None:
    db_path = tmp_path / "race_ingest.db"
    get_connection(db_path).close()
    svc_a = MemoryService(db_path)
    proposal = MemoryProposal(
        memory_type="recurring_merchant",
        scope="finance",
        subject="merge_race",
        confidence=0.5,
        payload={"cadence": "weekly"},
        source="detector:recurring_merchant",
        reason="first",
    )
    created = svc_a.ingest_proposals((proposal,), actor="detector")
    assert len(created) == 1
    mid = created[0].id

    follow_up = MemoryProposal(
        memory_type="recurring_merchant",
        scope="finance",
        subject="merge_race",
        confidence=0.6,
        payload={"typical_amount_cents": 500},
        source="detector:recurring_merchant",
        reason="second",
    )
    conn_b = get_connection(db_path)
    try:
        svc_b = MemoryService(
            db_path,
            conn=_proxy_conn_first_prior_row_fetch(
                conn_b,
                memory_type=follow_up.memory_type,
                scope=follow_up.scope,
                subject=follow_up.subject,
                between=lambda: svc_a.reject_memory(mid, actor="user", reason="no merge"),
            ),
        )
        with pytest.raises(ConflictError) as excinfo:
            svc_b.ingest_proposals((follow_up,), actor="detector")
        assert excinfo.value.data == {"memory_id": mid, "expected_status": "candidate"}
    finally:
        conn_b.close()
    row = svc_a.get_memory(mid)
    assert row.status == "rejected"
    assert row.payload == {"cadence": "weekly"}
    assert row.confidence == 0.5
    ev_types = [
        str(r["event_type"])
        for r in svc_a.conn.execute(
            "SELECT event_type FROM memory_events WHERE memory_id = ? ORDER BY id",
            (mid,),
        ).fetchall()
    ]
    assert ev_types == ["created", "rejected"]


def test_reject_memory_sets_expires_at_to_now_plus_30_days(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    before = datetime.now(UTC)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="ttl_reject",
        confidence=0.4,
        payload={},
        source="s",
        actor="user",
    )
    svc.reject_memory(rec.id, actor="user", reason="no")
    after = datetime.now(UTC)
    row = svc.conn.execute("SELECT expires_at FROM memories WHERE id = ?", (rec.id,)).fetchone()
    assert row is not None and row["expires_at"] is not None
    expires = datetime.fromisoformat(str(row["expires_at"]))
    expected_min = before + timedelta(days=30)
    expected_max = after + timedelta(days=30)
    assert expected_min - timedelta(seconds=1) <= expires <= expected_max + timedelta(seconds=1)


def test_reject_memory_expires_at_is_iso_utc(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="iso_utc",
        confidence=0.4,
        payload={},
        source="s",
        actor="user",
    )
    svc.reject_memory(rec.id, actor="user")
    raw = str(
        svc.conn.execute("SELECT expires_at FROM memories WHERE id = ?", (rec.id,)).fetchone()["expires_at"]
    )
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_prune_expired_memories_removes_expired_rejected_rows(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="prune_me",
        confidence=0.4,
        payload={},
        source="s",
        actor="user",
    )
    svc.reject_memory(rec.id, actor="user")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    svc.conn.execute("UPDATE memories SET expires_at = ? WHERE id = ?", (yesterday, rec.id))
    svc.conn.commit()
    n = svc.prune_expired_memories()
    assert n == 1
    row = svc.conn.execute("SELECT 1 FROM memories WHERE id = ?", (rec.id,)).fetchone()
    assert row is None


def test_prune_expired_memories_leaves_unrejected_rows_alone(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    for subject, confidence in (("a", 0.4), ("b", 0.9), ("c", 0.9)):
        svc.create_memory(
            memory_type="t",
            scope="s",
            subject=subject,
            confidence=confidence,
            payload={},
            source="s",
            actor="user",
        )
    svc.conn.execute("UPDATE memories SET expires_at = ?", (past,))
    svc.conn.commit()
    n = svc.prune_expired_memories()
    assert n == 0
    assert svc.conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"] == 3


def test_prune_expired_memories_leaves_rejected_but_unexpired_alone(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="still_here",
        confidence=0.4,
        payload={},
        source="s",
        actor="user",
    )
    svc.reject_memory(rec.id, actor="user")
    n = svc.prune_expired_memories()
    assert n == 0
    assert svc.get_memory(rec.id).status == "rejected"


def test_prune_expired_memories_respects_explicit_now_argument(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="future_prune",
        confidence=0.4,
        payload={},
        source="s",
        actor="user",
    )
    svc.reject_memory(rec.id, actor="user")
    far_future = datetime(2100, 1, 1, tzinfo=UTC)
    n = svc.prune_expired_memories(now=far_future)
    assert n == 1
    assert svc.conn.execute("SELECT 1 FROM memories WHERE id = ?", (rec.id,)).fetchone() is None


def test_memory_events_check_narrowed_rejects_vault_synced(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="t",
        scope="s",
        subject="ev_check",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    with pytest.raises(sqlite3.IntegrityError):
        svc.conn.execute(
            """
            INSERT INTO memory_events (memory_id, event_type, payload_json, actor, created_at)
            VALUES (?, 'vault_synced', '{}', 'system', datetime('now'))
            """,
            (rec.id,),
        )
    svc.conn.rollback()


def test_create_memory_rejects_invalid_payload(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    n = int(svc.conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"])
    with pytest.raises(InvalidInputError, match="invalid payload"):
        svc.create_memory(
            memory_type="preference",
            scope="core",
            subject="x",
            confidence=0.9,
            payload={"typo_field": 1},
            source="s",
            actor="user",
        )
    assert int(svc.conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]) == n


def test_ingest_proposals_skips_invalid_payloads_and_logs_warning(tmp_path, caplog) -> None:
    svc = _fresh_memory_service(tmp_path)
    bad = MemoryProposal(
        memory_type="preference",
        scope="s",
        subject="bad_subj",
        confidence=0.9,
        payload={"not_a_field": 1},
        source="detector:test",
        reason="r",
    )
    good = MemoryProposal(
        memory_type="preference",
        scope="s",
        subject="good_subj",
        confidence=0.9,
        payload={"note": "ok"},
        source="detector:test",
        reason="r2",
    )
    with caplog.at_level(logging.WARNING):
        out = svc.ingest_proposals((bad, good), actor="detector")
    assert len(out) == 1
    assert out[0].subject == "good_subj"
    assert "bad_subj" in caplog.text
    assert "preference" in caplog.text
    assert "detector:test" in caplog.text


def test_update_payload_rejects_invalid_payload_with_reread_type(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="u",
        confidence=0.9,
        payload={"value": "v0"},
        source="s",
        actor="user",
    )
    with pytest.raises(InvalidInputError, match="invalid payload"):
        svc.update_payload(rec.id, payload={"bogus": 1})


def test_update_payload_allows_valid_payload(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="u2",
        confidence=0.9,
        payload={},
        source="s",
        actor="user",
    )
    updated = svc.update_payload(rec.id, payload={"note": "hello", "value": "x"})
    assert updated.payload["note"] == "hello"
    assert updated.payload["value"] == "x"
