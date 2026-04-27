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


def _fake_github_token() -> str:
    return "".join(("gh", "p_", "a" * 36))


def _fake_private_key_block() -> str:
    return "\n".join(
        (
            "-----" + "BEGIN PRIVATE KEY" + "-----",
            "a" * 64,
            "-----" + "END PRIVATE KEY" + "-----",
        )
    )


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

        def execute(self, sql: str, parameters: Any = ()) -> Any:
            cur = self._inner.execute(sql, parameters)
            if (
                isinstance(sql, str)
                and "SELECT * FROM memories WHERE id = ?" in sql.strip()
                and tuple(parameters) == (memory_id,)
            ):

                class _Cursor:
                    def __init__(self, base: Any) -> None:
                        self._base = base
                        self._first_fetch = True

                    def fetchone(self) -> Any:
                        if self._first_fetch:
                            self._first_fetch = False
                            row = self._base.fetchone()
                            between()
                            return row
                        return self._base.fetchone()

                    def __getattr__(self, name: str) -> Any:
                        return getattr(self._base, name)

                return _Cursor(cur)
            return cur

        def __getattr__(self, name: str) -> Any:
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

        def execute(self, sql: str, parameters: Any = ()) -> Any:
            cur = self._inner.execute(sql, parameters)
            if (
                isinstance(sql, str)
                and "FROM memories" in sql
                and "ORDER BY updated_at DESC, id DESC" in sql
                and "LIMIT 1" in sql
                and tuple(parameters) == (memory_type, scope, subject)
            ):

                class _Cursor:
                    def __init__(self, base: Any) -> None:
                        self._base = base
                        self._first_fetch = True

                    def fetchone(self) -> Any:
                        if self._first_fetch:
                            self._first_fetch = False
                            row = self._base.fetchone()
                            between()
                            return row
                        return self._base.fetchone()

                    def __getattr__(self, name: str) -> Any:
                        return getattr(self._base, name)

                return _Cursor(cur)
            return cur

        def __getattr__(self, name: str) -> Any:
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


def test_list_memories_omits_expired_active_rows_when_status_is_none(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    active = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="fresh",
        confidence=0.9,
        payload={},
        source="test",
        actor="user",
    )
    expired = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="stale",
        confidence=0.9,
        payload={},
        source="test",
        actor="user",
    )
    svc.conn.execute(
        """
        UPDATE memories
        SET expires_at = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        ((datetime.now(UTC) - timedelta(days=1)).isoformat(), expired.id),
    )
    svc.conn.commit()

    assert [row.id for row in svc.list_memories(status=None)] == [active.id]
    assert [row.id for row in svc.list_memories(status="active")] == [active.id]


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
    assert "018_vault_index.sql" in names
    assert "019_playbook_runs.sql" in names
    assert "020_memory_content_fingerprint.sql" in names
    assert "021_memory_fts5.sql" in names
    assert "022_memory_edges.sql" in names
    assert "023_enrichment_queue.sql" in names
    assert "024_memory_embeddings.sql" in names
    assert names[-1] == "024_memory_embeddings.sql"


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

    Slice 6g: ``ConflictError.data`` gains ``conflict_kind`` and ``memory_id``.
    """
    from minx_mcp.contracts import ConflictError

    svc = _fresh_memory_service(tmp_path)
    created = svc.create_memory(
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
        "conflict_kind": "structural_triple",
        "memory_id": created.id,
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


def test_memory_events_check_allows_vault_synced_after_slice6c(tmp_path) -> None:
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
    svc.conn.execute(
        """
        INSERT INTO memory_events (memory_id, event_type, payload_json, actor, created_at)
        VALUES (?, 'vault_synced', '{}', 'vault_sync', datetime('now'))
        """,
        (rec.id,),
    )
    svc.conn.commit()


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
    assert len(out.failures) == 1
    assert out.failures[0].subject == "bad_subj"


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


# ======================================================================
# Slice 6g: Content Fingerprint Dedup — integration tests (§10.2 / §10.3)
# ======================================================================


def _fingerprint_of(svc: MemoryService, memory_id: int) -> str | None:
    row = svc.conn.execute(
        "SELECT content_fingerprint FROM memories WHERE id = ?", (memory_id,)
    ).fetchone()
    return None if row["content_fingerprint"] is None else str(row["content_fingerprint"])


def test_create_memory_redacts_secret_payload_and_audits_fields(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    secret = _fake_github_token()

    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="api_token_note",
        confidence=0.9,
        payload={"value": f"token {secret}"},
        source=f"detector {secret}",
        reason="captured from safe test fixture",
        actor="user",
    )

    assert rec.payload == {"value": "token [REDACTED:github_token]"}
    assert rec.source == "detector [REDACTED:github_token]"
    assert secret not in json.dumps(rec.payload)
    assert secret not in rec.source

    row = svc.conn.execute(
        "SELECT event_type, payload_json FROM memory_events WHERE memory_id = ? AND event_type = 'created'",
        (rec.id,),
    ).fetchone()
    assert row is not None
    event_payload = json.loads(str(row["payload_json"]))
    assert event_payload == {
        "secret_redacted": {
            "detected_kinds": ["github_token"],
            "fields": ["payload.value", "source"],
        }
    }

    from minx_mcp.core.fingerprint import content_fingerprint
    from minx_mcp.core.memory_service import _memory_fingerprint_input

    expected_fp = content_fingerprint(
        *_memory_fingerprint_input(
            "preference",
            rec.payload,
            scope="core",
            subject="api_token_note",
        )
    )
    assert _fingerprint_of(svc, rec.id) == expected_fp


def test_create_memory_blocks_secret_identity_and_writes_no_row(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    secret = _fake_github_token()

    with pytest.raises(InvalidInputError) as excinfo:
        svc.create_memory(
            memory_type="preference",
            scope="core",
            subject=secret,
            confidence=0.9,
            payload={"value": "safe"},
            source="user",
            actor="user",
        )

    assert excinfo.value.data["kind"] == "secret_detected"
    assert excinfo.value.data["surface"] == "memory"
    assert excinfo.value.data["detected_kinds"] == ["github_token"]
    assert secret not in json.dumps(excinfo.value.data)
    assert svc.conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"] == 0


def test_update_payload_blocks_private_key_and_leaves_row_unchanged(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="key_note",
        confidence=0.9,
        payload={"value": "safe"},
        source="user",
        actor="user",
    )
    before_fp = _fingerprint_of(svc, rec.id)
    before_events = svc.conn.execute("SELECT COUNT(*) AS c FROM memory_events").fetchone()["c"]

    with pytest.raises(InvalidInputError) as excinfo:
        svc.update_payload(rec.id, payload={"value": _fake_private_key_block()})

    assert excinfo.value.data["kind"] == "secret_detected"
    assert svc.get_memory(rec.id).payload == {"value": "safe"}
    assert _fingerprint_of(svc, rec.id) == before_fp
    assert svc.conn.execute("SELECT COUNT(*) AS c FROM memory_events").fetchone()["c"] == before_events


def test_update_payload_redacts_secret_and_audits_payload_updated_event(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    secret = _fake_github_token()
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="update_redact",
        confidence=0.9,
        payload={"value": "safe"},
        source="user",
        actor="user",
    )

    updated = svc.update_payload(rec.id, payload={"value": secret})

    assert updated.payload == {"value": "[REDACTED:github_token]"}
    event = svc.conn.execute(
        """
        SELECT payload_json FROM memory_events
        WHERE memory_id = ? AND event_type = 'payload_updated'
        ORDER BY id DESC LIMIT 1
        """,
        (rec.id,),
    ).fetchone()
    event_payload = json.loads(str(event["payload_json"]))
    assert event_payload == {
        "payload": {"value": "[REDACTED:github_token]"},
        "secret_redacted": {"detected_kinds": ["github_token"], "fields": ["payload.value"]},
    }
    assert secret not in json.dumps(event_payload)


def test_reject_and_expire_memory_redact_secret_reasons_in_events(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    secret = _fake_github_token()
    rejected = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="reject_reason",
        confidence=0.5,
        payload={"value": "safe"},
        source="user",
        actor="user",
    )
    expired = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="expire_reason",
        confidence=0.9,
        payload={"value": "safe"},
        source="user",
        actor="user",
    )

    svc.reject_memory(rejected.id, actor="user", reason=secret)
    svc.expire_memory(expired.id, actor="user", reason=secret)

    payloads = [
        json.loads(str(row["payload_json"]))
        for row in svc.conn.execute(
            "SELECT payload_json FROM memory_events WHERE event_type IN ('rejected', 'expired') ORDER BY id"
        ).fetchall()
    ]
    assert payloads == [
        {
            "reason": "[REDACTED:github_token]",
            "secret_redacted": {"detected_kinds": ["github_token"], "fields": ["reason"]},
        },
        {
            "reason": "[REDACTED:github_token]",
            "secret_redacted": {"detected_kinds": ["github_token"], "fields": ["reason"]},
        },
    ]
    assert secret not in json.dumps(payloads)


def test_ingest_proposals_secret_identity_records_sanitized_failure(tmp_path, caplog) -> None:
    svc = _fresh_memory_service(tmp_path)
    secret = _fake_github_token()
    proposal = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject=secret,
        confidence=0.9,
        payload={"not_a_field": _fake_private_key_block()},
        source=f"detector {secret}",
        reason=f"reason {secret}",
    )

    with caplog.at_level(logging.WARNING):
        report = svc.ingest_proposals((proposal,), actor="detector")

    assert report.succeeded == []
    assert report.suppressed == []
    assert report.failures == [
        type(report.failures[0])(
            memory_type="preference",
            scope="core",
            subject="[REDACTED_SUBJECT]",
            reason="secret_detected",
        )
    ]
    assert secret not in caplog.text
    assert secret not in repr(report.failures)
    assert svc.conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"] == 0


def test_ingest_proposals_rejected_prior_suppresses_before_secret_payload_value_scan(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    first = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="rejected_secret_payload",
        confidence=0.5,
        payload={"value": "safe"},
        source="user",
        actor="user",
    )
    svc.reject_memory(first.id, actor="user")
    proposal = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="rejected_secret_payload",
        confidence=0.9,
        payload={"value": _fake_private_key_block()},
        source="detector:test",
        reason="again",
    )

    report = svc.ingest_proposals((proposal,), actor="detector")

    assert report.failures == []
    assert len(report.suppressed) == 1
    assert report.suppressed[0].reason == "structural_rejected_prior"


def test_ingest_proposals_uses_fixed_reason_for_invalid_payload(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    bad = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="bad_payload",
        confidence=0.9,
        payload={"not_a_field": 1},
        source="detector:test",
        reason="bad",
    )

    report = svc.ingest_proposals((bad,), actor="detector")

    assert report.failures[0].reason == "invalid_payload"


def test_search_memories_finds_active_memory_by_payload_value(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    record = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "prefers espresso after training"},
        source="user",
        reason="manual",
    )

    results = svc.search_memories(query="espresso", limit=10)

    assert [result.memory.id for result in results] == [record.id]
    assert "espresso" in results[0].snippet.lower()


def test_search_memories_updates_index_when_payload_changes(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    record = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="drink",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )

    svc.update_payload(record.id, payload={"value": "tea"}, actor="user")

    assert svc.search_memories(query="espresso") == []
    assert [result.memory.id for result in svc.search_memories(query="tea")] == [record.id]


def test_search_memories_defaults_to_active_status(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    active = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="active",
        confidence=0.95,
        payload={"value": "matchable"},
        source="user",
        reason="manual",
    )
    rejected = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="rejected",
        confidence=0.4,
        payload={"value": "matchable"},
        source="user",
        reason="manual",
    )
    svc.reject_memory(rejected.id, actor="user", reason="no")

    assert [result.memory.id for result in svc.search_memories(query="matchable")] == [active.id]
    assert {result.memory.id for result in svc.search_memories(query="matchable", status=None)} == {
        active.id,
        rejected.id,
    }


def test_search_memories_scope_and_type_filters(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    keep = svc.create_memory(
        memory_type="preference",
        scope="finance",
        subject="merchant",
        confidence=0.95,
        payload={"value": "coffee"},
        source="user",
        reason="manual",
    )
    svc.create_memory(
        memory_type="constraint",
        scope="finance",
        subject="budget",
        confidence=0.95,
        payload={"limit_value": "coffee"},
        source="user",
        reason="manual",
    )

    results = svc.search_memories(query="coffee", scope="finance", memory_type="preference")

    assert [result.memory.id for result in results] == [keep.id]


def test_search_memories_rejects_invalid_query_and_limit(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)

    with pytest.raises(InvalidInputError):
        svc.search_memories(query='"unterminated')
    with pytest.raises(InvalidInputError):
        svc.search_memories(query="coffee", limit=0)


def test_search_memories_excludes_expired_active_rows(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    record = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )
    svc.conn.execute(
        "UPDATE memories SET expires_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(days=1)).isoformat(), record.id),
    )
    svc.conn.commit()

    assert svc.list_active_memories() == []
    assert svc.search_memories(query="espresso") == []


def test_search_memories_skips_legacy_rows_with_malformed_payload_json(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    valid = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="valid legacy",
        confidence=0.95,
        payload={"value": "legacy"},
        source="user",
        reason="manual",
    )
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status, payload_json, source, reason
            ) VALUES ('preference', 'core', 'broken legacy', 0.95, 'active', '{not-json', 'legacy', '')
        """
    )
    svc.conn.commit()

    assert [result.memory.id for result in svc.search_memories(query="legacy")] == [valid.id]


def test_ingest_proposals_legacy_secret_merge_records_failure_without_batch_abort(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    private_key = _fake_private_key_block()
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status, payload_json, source, reason
        ) VALUES (?, ?, ?, 0.6, 'candidate', ?, 'legacy', 'legacy import')
        """,
        ("unknown_type", "core", "legacy", json.dumps({"legacy_secret": private_key})),
    )
    svc.conn.commit()
    safe = MemoryProposal(
        memory_type="unknown_type",
        scope="core",
        subject="safe",
        confidence=0.9,
        payload={"safe": "value"},
        source="detector",
        reason="safe proposal",
    )
    merging = MemoryProposal(
        memory_type="unknown_type",
        scope="core",
        subject="legacy",
        confidence=0.7,
        payload={"safe": "value"},
        source="detector",
        reason="merge proposal",
    )

    report = svc.ingest_proposals([safe, merging], actor="detector")

    assert [record.subject for record in report.succeeded] == ["safe"]
    assert len(report.failures) == 1
    assert report.failures[0].reason == "secret_detected"
    assert private_key not in repr(report)


def test_memory_edges_create_list_and_delete(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    source = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="new",
        confidence=0.95,
        payload={"value": "new value"},
        source="user",
        reason="manual",
    )
    target = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="old",
        confidence=0.95,
        payload={"value": "old value"},
        source="user",
        reason="manual",
    )

    edge = svc.create_memory_edge(
        source_memory_id=source.id,
        target_memory_id=target.id,
        predicate="supersedes",
        relation_note="newer version",
        actor="user",
    )

    assert edge.source_memory_id == source.id
    assert edge.target_memory_id == target.id
    assert edge.predicate == "supersedes"
    assert [listed.id for listed in svc.list_memory_edges(source.id, direction="outgoing")] == [edge.id]
    assert [listed.id for listed in svc.list_memory_edges(target.id, direction="incoming")] == [edge.id]
    assert svc.delete_memory_edge(edge.id) is True
    assert svc.list_memory_edges(source.id) == []
    assert svc.delete_memory_edge(edge.id) is False


def test_memory_edges_reject_invalid_predicate_self_edge_and_duplicates(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    source = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="source",
        confidence=0.95,
        payload={"value": "source"},
        source="user",
        reason="manual",
    )
    target = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="target",
        confidence=0.95,
        payload={"value": "target"},
        source="user",
        reason="manual",
    )

    with pytest.raises(InvalidInputError):
        svc.create_memory_edge(
            source_memory_id=source.id,
            target_memory_id=target.id,
            predicate="explains",
            actor="user",
        )
    with pytest.raises(InvalidInputError):
        svc.create_memory_edge(
            source_memory_id=source.id,
            target_memory_id=source.id,
            predicate="cites",
            actor="user",
        )

    svc.create_memory_edge(
        source_memory_id=source.id,
        target_memory_id=target.id,
        predicate="cites",
        actor="user",
    )
    with pytest.raises(ConflictError):
        svc.create_memory_edge(
            source_memory_id=source.id,
            target_memory_id=target.id,
            predicate="cites",
            actor="user",
        )


def test_memory_edge_relation_note_redacts_secrets(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    secret = _fake_github_token()
    source = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="source",
        confidence=0.95,
        payload={"value": "source"},
        source="user",
        reason="manual",
    )
    target = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="target",
        confidence=0.95,
        payload={"value": "target"},
        source="user",
        reason="manual",
    )

    edge = svc.create_memory_edge(
        source_memory_id=source.id,
        target_memory_id=target.id,
        predicate="contradicts",
        relation_note=f"token {secret}",
        actor="user",
    )

    assert edge.relation_note == "token [REDACTED:github_token]"
    assert secret not in repr(edge)


def test_content_fingerprint_conflict_redacts_historical_existing_subject(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    secret = _fake_github_token()
    first = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="safe_subject",
        confidence=0.9,
        payload={"value": "sparkling water"},
        source="user",
        actor="user",
    )
    from minx_mcp.core.fingerprint import content_fingerprint
    from minx_mcp.core.memory_service import _memory_fingerprint_input

    colliding_fp = content_fingerprint(
        *_memory_fingerprint_input(
            "preference",
            {"value": "sparkling water"},
            scope="core",
            subject="different_subject",
        )
    )
    svc.conn.execute(
        "UPDATE memories SET subject = ?, content_fingerprint = ? WHERE id = ?",
        (secret, colliding_fp, first.id),
    )
    svc.conn.commit()

    with pytest.raises(ConflictError) as excinfo:
        svc.create_memory(
            memory_type="preference",
            scope="core",
            subject="different_subject",
            confidence=0.9,
            payload={"value": "sparkling water"},
            source="user",
            actor="user",
        )

    assert excinfo.value.data["conflict_kind"] == "content_fingerprint"
    assert excinfo.value.data["memory_id"] == first.id
    assert secret not in json.dumps(excinfo.value.data)
    assert excinfo.value.data.get("existing_subject") in {None, "[REDACTED_EXISTING_SUBJECT]"}


def test_create_memory_persists_content_fingerprint(tmp_path) -> None:
    """§10.2: all known types persist a non-null fingerprint on create."""
    svc = _fresh_memory_service(tmp_path)

    pref = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="tz",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="user",
        actor="user",
    )
    pat = svc.create_memory(
        memory_type="pattern",
        scope="focus",
        subject="morning",
        confidence=0.7,
        payload={"signal": "deep_work"},
        source="user",
        actor="user",
    )
    ent = svc.create_memory(
        memory_type="entity_fact",
        scope="people",
        subject="nate",
        confidence=0.9,
        payload={"aliases": ["Nate", "NATHAN"]},
        source="user",
        actor="user",
    )
    con = svc.create_memory(
        memory_type="constraint",
        scope="spend",
        subject="groceries",
        confidence=0.9,
        payload={"limit_value": "150"},
        source="user",
        actor="user",
    )

    for rec in (pref, pat, ent, con):
        fp = _fingerprint_of(svc, rec.id)
        assert fp is not None, f"{rec.memory_type} row missing fingerprint"
        assert len(fp) == 64, f"{rec.memory_type} fingerprint is not sha256 hex"


def test_create_memory_content_fingerprint_collision_raises_conflict(tmp_path) -> None:
    """§10.2: different-case subjects with equivalent content collide on the partial unique index."""
    svc = _fresh_memory_service(tmp_path)
    first = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="Netflix",
        confidence=0.8,
        payload={"value": "yes"},
        source="user",
        actor="user",
    )
    with pytest.raises(ConflictError) as excinfo:
        svc.create_memory(
            memory_type="preference",
            scope="core",
            subject="netflix",
            confidence=0.5,
            payload={"value": "yes"},
            source="user",
            actor="user",
        )
    assert excinfo.value.data == {
        "conflict_kind": "content_fingerprint",
        "memory_id": first.id,
        "existing_subject": "Netflix",
        "memory_type": "preference",
        "scope": "core",
        "subject": "netflix",
    }


def test_update_payload_content_fingerprint_collision_raises_conflict(tmp_path) -> None:
    """§10.2: update_payload collision with another live row surfaces as typed CONFLICT.

    Under normal operation the row-fixed ``(memory_type, scope, subject)`` tuple
    guarantees two live rows cannot share a content fingerprint (the structural
    unique index prevents same-triple duplicates). The ``content_fingerprint_update``
    path still ships as defence-in-depth against stale / manually-corrupted
    fingerprints (e.g., from a partial backfill, or an operator fixing a row via
    SQLite shell). This test simulates that scenario by poisoning row A's stored
    fingerprint to match what row B's update WILL produce, then asserting the
    typed error maps correctly.
    """
    from minx_mcp.core.fingerprint import content_fingerprint
    from minx_mcp.core.memory_service import _memory_fingerprint_input

    svc = _fresh_memory_service(tmp_path)
    a = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="snack",
        confidence=0.9,
        payload={"value": "popcorn"},
        source="user",
        actor="user",
    )
    b = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="drink",
        confidence=0.9,
        payload={"value": "water"},
        source="user",
        actor="user",
    )

    # Poison row A's stored fingerprint to match what row B's new fingerprint
    # WILL be once we update B's payload.
    new_b_payload = {"value": "soda"}
    fp_new_b = content_fingerprint(
        *_memory_fingerprint_input(
            "preference",
            new_b_payload,
            scope="core",
            subject="drink",
        )
    )
    svc.conn.execute(
        "UPDATE memories SET content_fingerprint = ? WHERE id = ?",
        (fp_new_b, a.id),
    )
    svc.conn.commit()

    with pytest.raises(ConflictError) as excinfo:
        svc.update_payload(b.id, payload=new_b_payload)
    assert excinfo.value.data["conflict_kind"] == "content_fingerprint_update"
    assert excinfo.value.data["memory_id"] == b.id
    assert excinfo.value.data["blocking_memory_id"] == a.id


def test_ingest_proposals_content_equivalence_merges_across_case(tmp_path) -> None:
    """§10.2 load-bearing v5 fix: different subject casing, same content → single row, merged event."""
    svc = _fresh_memory_service(tmp_path)
    existing = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="Netflix",
        confidence=0.5,
        payload={"value": "yes"},
        source="user",
        actor="user",
    )
    prop = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="netflix",
        confidence=0.9,
        payload={"value": "yes"},
        source="detector:test",
        reason="case-collision",
    )
    report = svc.ingest_proposals((prop,), actor="detector")
    assert report.failures == []
    assert report.suppressed == []
    assert len(report.succeeded) == 1
    assert report.succeeded[0].id == existing.id

    live_rows = svc.conn.execute(
        "SELECT id FROM memories WHERE status IN ('candidate','active')"
    ).fetchall()
    assert [r["id"] for r in live_rows] == [existing.id]

    events = svc.conn.execute(
        """
        SELECT event_type, payload_json
        FROM memory_events
        WHERE memory_id = ?
        ORDER BY id
        """,
        (existing.id,),
    ).fetchall()
    merge_events = [
        (e["event_type"], json.loads(e["payload_json"])) for e in events if e["event_type"] == "payload_updated"
    ]
    assert merge_events, "expected a payload_updated event from content-equivalence merge"
    evtype, payload_obj = merge_events[-1]
    assert evtype == "payload_updated"
    assert payload_obj.get("merge_trigger") == "content_fingerprint"
    assert payload_obj.get("prior_identity") == {
        "memory_type": "preference",
        "scope": "core",
        "subject": "netflix",
    }


def test_ingest_proposals_same_triple_merge_updates_fingerprint(tmp_path) -> None:
    """§10.2 'same-row overlap' case: one row remains; fp stays consistent with merged payload."""
    svc = _fresh_memory_service(tmp_path)
    p1 = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="pizza",
        confidence=0.5,
        payload={"value": "cheese"},
        source="detector:test",
        reason="first",
    )
    p2 = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="pizza",
        confidence=0.7,
        payload={"value": "cheese", "note": "favorite"},
        source="detector:test",
        reason="update",
    )
    report = svc.ingest_proposals((p1, p2), actor="detector")
    assert report.failures == []
    assert report.suppressed == []
    assert len(report.succeeded) == 2
    assert report.succeeded[0].id == report.succeeded[1].id
    live_rows = svc.conn.execute(
        "SELECT id FROM memories WHERE status IN ('candidate','active')"
    ).fetchall()
    assert len(live_rows) == 1

    fp = _fingerprint_of(svc, report.succeeded[0].id)
    assert fp is not None and len(fp) == 64


def test_ingest_proposals_structural_rejected_prior_suppresses(tmp_path) -> None:
    """§10.2: rejected structural prior → suppressed, not created, not failed."""
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.5,
        payload={"value": "yes"},
        source="user",
        actor="user",
    )
    svc.reject_memory(rec.id, reason="user said no", actor="user")

    prop = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.9,
        payload={"value": "yes"},
        source="detector:test",
        reason="reappeared",
    )
    report = svc.ingest_proposals((prop,), actor="detector")
    assert report.succeeded == []
    assert report.failures == []
    assert len(report.suppressed) == 1
    suppression = report.suppressed[0]
    assert suppression.memory_type == "preference"
    assert suppression.scope == "core"
    assert suppression.subject == "coffee"
    assert suppression.reason == "structural_rejected_prior"


def test_ingest_proposals_structural_rejected_prior_suppresses_invalid_payload(tmp_path) -> None:
    """§10.2: invalid payload + rejected structural prior → suppression, *not* failure (ordering guard)."""
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="gym",
        confidence=0.5,
        payload={"value": "daily"},
        source="user",
        actor="user",
    )
    svc.reject_memory(rec.id, reason="not relevant", actor="user")

    prop = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="gym",
        confidence=0.9,
        payload={"bogus_key": 1},
        source="detector:test",
        reason="bad",
    )
    report = svc.ingest_proposals((prop,), actor="detector")
    assert report.succeeded == []
    assert report.failures == []
    assert len(report.suppressed) == 1
    assert report.suppressed[0].reason == "structural_rejected_prior"


def test_ingest_proposals_content_fingerprint_rejected_prior_suppresses(tmp_path) -> None:
    """§10.2: rejected-fingerprint prior at a different triple → suppression under new triple."""
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="Netflix",
        confidence=0.7,
        payload={"value": "yes"},
        source="user",
        actor="user",
    )
    svc.reject_memory(rec.id, reason="not a preference", actor="user")

    prop = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="netflix",
        confidence=0.9,
        payload={"value": "yes"},
        source="detector:test",
        reason="recurred",
    )
    report = svc.ingest_proposals((prop,), actor="detector")
    assert report.succeeded == []
    assert report.failures == []
    assert len(report.suppressed) == 1
    assert report.suppressed[0].reason == "content_fingerprint_rejected_prior"


def test_reject_memory_releases_fingerprint_slot(tmp_path) -> None:
    """§10.2: reject releases the partial-unique-index slot; new same-content create succeeds."""
    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="walk",
        confidence=0.5,
        payload={"value": "morning"},
        source="user",
        actor="user",
    )
    svc.reject_memory(rec.id, reason="irrelevant", actor="user")

    # Now we create a row with the same content AT A DIFFERENT TRIPLE (same
    # content fingerprint). Without the partial (live-only) index, this would
    # still collide. With it, rejected rows no longer compete.
    fresh = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="stroll",
        confidence=0.7,
        payload={"value": "morning"},
        source="user",
        actor="user",
    )
    assert fresh.id != rec.id
    assert _fingerprint_of(svc, fresh.id) is not None


def test_expire_memory_releases_fingerprint_slot(tmp_path) -> None:
    """§10.2: expire releases the partial-unique-index slot the same way reject does."""
    svc = _fresh_memory_service(tmp_path)
    # create_memory with confidence >= 0.8 already lands as active, so expire
    # can be called directly. If confidence < 0.8, confirm first.
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="Netflix",
        confidence=0.9,
        payload={"value": "yes"},
        source="user",
        actor="user",
    )
    svc.expire_memory(rec.id, actor="system")

    # Different triple, same content → must succeed since the prior row expired.
    fresh = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="Hulu",
        confidence=0.6,
        payload={"value": "yes"},
        source="user",
        actor="user",
    )
    assert fresh.id != rec.id


def test_ingest_proposals_report_equality_includes_suppressed(tmp_path) -> None:
    """§10.3: IngestProposalsReport.__eq__ default compares all three lists."""
    from minx_mcp.core.memory_service import IngestProposalsReport, MemoryProposalSuppression

    empty = IngestProposalsReport(succeeded=[], failures=[], suppressed=[])
    with_suppression = IngestProposalsReport(
        succeeded=[],
        failures=[],
        suppressed=[
            MemoryProposalSuppression(
                memory_type="preference",
                scope="core",
                subject="x",
                reason="structural_rejected_prior",
            )
        ],
    )
    assert empty == empty
    assert empty != with_suppression
    assert with_suppression == with_suppression


def test_ingest_proposals_report_equality_with_list_unchanged(tmp_path) -> None:
    """§10.3: report == [records] behavior unchanged by the new suppressed field."""
    svc = _fresh_memory_service(tmp_path)
    prop = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="reading",
        confidence=0.8,
        payload={"value": "novels"},
        source="detector:test",
        reason="first",
    )
    report = svc.ingest_proposals((prop,), actor="detector")
    assert report == report.succeeded
    assert list(report) == report.succeeded


@pytest.mark.parametrize(
    ("memory_type", "scope", "subject", "payload"),
    [
        ("preference", "meals", "friday", {"category": "food", "value": "pizza", "note": "Friday nights"}),
        ("pattern", "focus", "morning", {"signal": "deep_work", "note": "best 9-11am"}),
        ("entity_fact", "people", "nate", {"aliases": ["Nate", "NATHAN"], "note": "coworker"}),
        ("constraint", "spend", "groceries", {"limit_value": "150", "note": "weekly budget"}),
    ],
    ids=["preference", "pattern", "entity_fact", "constraint"],
)
def test_per_type_fingerprint_matches_helper(
    tmp_path, memory_type: str, scope: str, subject: str, payload: dict[str, object]
) -> None:
    """Stored fingerprint equals content_fingerprint(*_memory_fingerprint_input(...))
    for every known type (§10.2 L1140).
    """
    from minx_mcp.core.fingerprint import content_fingerprint
    from minx_mcp.core.memory_service import _memory_fingerprint_input

    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type=memory_type,
        scope=scope,
        subject=subject,
        confidence=0.8,
        payload=payload,
        source="user",
        actor="user",
    )
    expected_parts = _memory_fingerprint_input(
        memory_type,
        dict(rec.payload),
        scope=scope,
        subject=subject,
    )
    expected_fp = content_fingerprint(*expected_parts)
    assert _fingerprint_of(svc, rec.id) == expected_fp


def test_unknown_memory_type_fallback_fingerprint(tmp_path) -> None:
    """§10.2: unknown types fall through to JSON-canonical payload fingerprint.

    Since ``validate_memory_payload`` *rejects* unknown types at the public
    ``create_memory`` boundary, we simulate a pre-existing unknown-type row by
    writing it directly and asserting ``_memory_fingerprint_input``'s
    unknown-type fallback is what the backfill / dedup path would produce.
    """
    from minx_mcp.core.fingerprint import content_fingerprint
    from minx_mcp.core.memory_service import _memory_fingerprint_input

    payload = {"foo": "bar", "baz": 123}
    parts = _memory_fingerprint_input(
        "zzz_unregistered_type",
        payload,
        scope="odd",
        subject="row",
    )
    assert parts == (
        "zzz_unregistered_type",
        "odd",
        "row",
        "",
        json.dumps(payload, sort_keys=True, ensure_ascii=False),
    )
    # And ensure content_fingerprint is a valid sha256 hex.
    fp = content_fingerprint(*parts)
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)


def test_content_equivalence_merge_promotes_candidate_to_active(tmp_path) -> None:
    """Content-equivalence merge promotes candidate→active when max confidence ≥ 0.8
    and emits a 'promoted' event (§10.2 L1132).
    """
    svc = _fresh_memory_service(tmp_path)
    # Seed a candidate row (confidence 0.5 < 0.8 → candidate).
    existing = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="Spotify",
        confidence=0.5,
        payload={"value": "premium"},
        source="user",
        actor="user",
    )
    assert existing.status == "candidate"

    # Cross-triple proposal with bumping confidence — should trip the merge
    # AND promote to active AND emit a 'promoted' event on the matched row.
    prop = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="spotify",
        confidence=0.9,
        payload={"value": "premium"},
        source="detector:test",
        reason="case-collision-promotion",
    )
    report = svc.ingest_proposals((prop,), actor="detector")
    assert report.failures == []
    assert report.suppressed == []
    assert len(report.succeeded) == 1
    assert report.succeeded[0].id == existing.id

    after = svc.get_memory(existing.id)
    assert after.status == "active", "merge with max confidence ≥ 0.8 must promote"
    assert after.confidence == 0.9

    events = svc.conn.execute(
        """
        SELECT event_type, payload_json
        FROM memory_events
        WHERE memory_id = ?
        ORDER BY id
        """,
        (existing.id,),
    ).fetchall()
    event_types = [e["event_type"] for e in events]
    assert "payload_updated" in event_types
    assert "promoted" in event_types, (
        "content-equivalence merge must emit 'promoted' alongside 'payload_updated' "
        "when auto-promoting (parallels structural merge semantics)"
    )


def test_content_equivalence_merge_skip_write_short_circuits(tmp_path) -> None:
    """§10.2 L1133: content-equivalence merge produces no UPDATE and no event when everything matches."""
    svc = _fresh_memory_service(tmp_path)
    existing = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="Hulu",
        confidence=0.9,
        payload={"value": "yes"},
        source="user",
        reason="seed-reason",
        actor="user",
    )
    # Snapshot events and updated_at before the second ingest.
    events_before = svc.conn.execute(
        "SELECT id FROM memory_events WHERE memory_id = ? ORDER BY id",
        (existing.id,),
    ).fetchall()
    updated_before = svc.conn.execute(
        "SELECT updated_at FROM memories WHERE id = ?", (existing.id,)
    ).fetchone()["updated_at"]

    # Cross-triple proposal (case difference) with byte-identical payload,
    # same confidence, same reason. The merge branch should short-circuit:
    # no UPDATE issued, no payload_updated event emitted.
    prop = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="HULU",
        confidence=0.9,
        payload={"value": "yes"},
        source="detector:test",
        reason="seed-reason",
    )
    report = svc.ingest_proposals((prop,), actor="detector")
    assert report.failures == []
    assert report.suppressed == []
    assert len(report.succeeded) == 1
    assert report.succeeded[0].id == existing.id

    events_after = svc.conn.execute(
        "SELECT id FROM memory_events WHERE memory_id = ? ORDER BY id",
        (existing.id,),
    ).fetchall()
    assert [e["id"] for e in events_after] == [e["id"] for e in events_before], (
        "skip-write branch must not emit any new memory_events"
    )

    updated_after = svc.conn.execute(
        "SELECT updated_at FROM memories WHERE id = ?", (existing.id,)
    ).fetchone()["updated_at"]
    assert updated_after == updated_before, (
        "skip-write branch must not bump updated_at (would indicate a stealth UPDATE)"
    )


def test_ingest_proposals_expired_fingerprint_reopens_fresh_row(tmp_path) -> None:
    """A proposal matching an expired fingerprint creates a fresh row
    (existing expired-reopen contract, §10.2 L1137).
    """
    svc = _fresh_memory_service(tmp_path)
    existing = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="Disney",
        confidence=0.9,
        payload={"value": "yes"},
        source="user",
        actor="user",
    )
    expired_id = existing.id
    svc.expire_memory(existing.id, actor="system")
    stored_fp = _fingerprint_of(svc, expired_id)
    assert stored_fp is not None

    prop = MemoryProposal(
        memory_type="preference",
        scope="core",
        subject="Disney",
        confidence=0.9,
        payload={"value": "yes"},
        source="user",
        reason="resurfaced",
    )
    report = svc.ingest_proposals((prop,), actor="detector")
    assert report.failures == []
    assert report.suppressed == []
    assert len(report.succeeded) == 1

    new_row = report.succeeded[0]
    assert new_row.id != expired_id, "expired fingerprint must NOT prevent re-insertion"
    assert new_row.status in {"candidate", "active"}

    fresh_fp = _fingerprint_of(svc, new_row.id)
    assert fresh_fp == stored_fp, (
        "the fresh row should fingerprint identically to the expired one; "
        "the partial unique index lets them coexist because the expired row "
        "is excluded from the WHERE status IN ('candidate','active') predicate"
    )

    live_ids = [
        r["id"]
        for r in svc.conn.execute(
            "SELECT id FROM memories WHERE status IN ('candidate','active') AND content_fingerprint = ?",
            (fresh_fp,),
        ).fetchall()
    ]
    assert live_ids == [new_row.id]


def test_corrupted_payload_fingerprints_as_degraded_tuple(tmp_path) -> None:
    """Rows whose payload_json fails coercion degrade to
    content_fingerprint(type, scope, subject, '', '') (§10.2 L1142).
    """
    from minx_mcp.core.fingerprint import content_fingerprint

    svc = _fresh_memory_service(tmp_path)
    # Seed a legitimate row so we have an anchor, then poke a corrupted row
    # directly into SQLite. The service's coercion path would normally reject
    # such junk, but this test simulates a pre-6a row surfacing via backfill.
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, status, confidence,
            payload_json, source, reason, created_at, updated_at
        ) VALUES (
            'preference', 'core', 'broken',
            'active', 0.9,
            '{"not_a_preference_field": true, "also_junk": 42}',
            'legacy', '', datetime('now'), datetime('now')
        )
        """
    )
    svc.conn.commit()
    _broken_row_exists = int(
        svc.conn.execute(
            "SELECT id FROM memories WHERE subject = 'broken'"
        ).fetchone()["id"]
    )
    assert _broken_row_exists > 0, "seeded row must be queryable"

    # Now verify the degraded-dedup path computes the correct fingerprint for
    # this kind of row. We reuse _compute_fingerprint_for_row (the backfill
    # helper) which is the canonical degraded-dedup implementation.
    from scripts.backfill_memory_fingerprints import _compute_fingerprint_for_row

    degraded_fp = _compute_fingerprint_for_row(
        memory_type="preference",
        scope="core",
        subject="broken",
        payload_json='{"not_a_preference_field": true, "also_junk": 42}',
    )
    expected_fp = content_fingerprint("preference", "core", "broken", "", "")
    assert degraded_fp == expected_fp, (
        "a row whose payload has no preference fields (value/note) must "
        "degrade to content_fingerprint(type, scope, subject, '', '') "
        "per §5.2 degraded-dedup path"
    )


def test_empty_content_clean_row_fingerprints_as_degraded_5tuple(tmp_path) -> None:
    """A preference with value=None,note=None fingerprints to
    content_fingerprint(type, scope, subject, '', '') (§10.2 L1143).

    This documents that a "clean" empty-content row and a "corrupted" row on
    the same triple collapse to the exact same fingerprint — i.e. the
    degraded-dedup 5-tuple per §5.2. The partial unique index on the content
    fingerprint then protects against *cross-triple* duplicates sharing that
    tuple, but a second row with the SAME triple would always hit the
    structural unique index (on memory_type/scope/subject) first, so we only
    assert the fingerprint equivalence here, not a content-index collision
    for two rows sharing every field.
    """
    from minx_mcp.core.fingerprint import content_fingerprint

    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="emptyfield",
        confidence=0.9,
        payload={"category": "misc"},  # no 'value', no 'note' keys
        source="user",
        actor="user",
    )
    stored_fp = _fingerprint_of(svc, rec.id)
    expected_fp = content_fingerprint("preference", "core", "emptyfield", "", "")
    assert stored_fp == expected_fp, (
        "a preference with no value/note fingerprints to "
        "content_fingerprint(type, scope, subject, '', '') — the same "
        "degraded tuple as a corrupted row with this triple (§5.2)"
    )

    # Sibling row on a *different triple* but also empty-content: its
    # fingerprint changes only in the subject slot, confirming the degraded
    # 5-tuple stays content-free but remains triple-scoped (no cross-triple
    # false collision).
    sibling = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="emptyfield2",
        confidence=0.9,
        payload={"category": "other"},
        source="user",
        actor="user",
    )
    sibling_fp = _fingerprint_of(svc, sibling.id)
    assert sibling_fp == content_fingerprint(
        "preference", "core", "emptyfield2", "", ""
    )
    assert sibling_fp != stored_fp, (
        "degraded 5-tuple must still differ across subjects — the content-index "
        "only matches on identical tuples"
    )


def test_entity_fact_alias_order_and_unicode_stable(tmp_path) -> None:
    """aliases=[NFC,upper] and aliases=[NFD,lower] fingerprint identically
    (canonical normalization + sort, §10.2 L1144).
    """
    from minx_mcp.core.fingerprint import content_fingerprint
    from minx_mcp.core.memory_service import _memory_fingerprint_input

    svc = _fresh_memory_service(tmp_path)
    rec = svc.create_memory(
        memory_type="entity_fact",
        scope="people",
        subject="cafe_lover",
        confidence=0.9,
        payload={"aliases": ["café", "NETFLIX"]},  # NFC é, uppercase
        source="user",
        actor="user",
    )
    stored_fp = _fingerprint_of(svc, rec.id)

    # Recompute what an NFD+lowercase aliases payload would produce on the
    # same triple. It must fingerprint identically after normalization.
    nfd_parts = _memory_fingerprint_input(
        "entity_fact",
        {"aliases": ["cafe\u0301", "netflix"]},  # NFD e + combining acute, lowercase
        scope="people",
        subject="cafe_lover",
    )
    equivalent_fp = content_fingerprint(*nfd_parts)
    assert stored_fp == equivalent_fp, (
        "entity_fact aliases must be stable under NFC/NFD and case differences — "
        "_canonical_aliases normalizes each alias and sorts the result"
    )

    # And reversing the alias order must not change the fingerprint.
    reversed_parts = _memory_fingerprint_input(
        "entity_fact",
        {"aliases": ["NETFLIX", "café"]},  # same entries, different order
        scope="people",
        subject="cafe_lover",
    )
    reversed_fp = content_fingerprint(*reversed_parts)
    assert stored_fp == reversed_fp, "alias order must not affect the fingerprint"
