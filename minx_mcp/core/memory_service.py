"""SQLite-backed memory CRUD and proposal ingestion (Slice 6a)."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection
from typing import Any, cast

from minx_mcp.base_service import BaseService
from minx_mcp.contracts import ConflictError, InvalidInputError, NotFoundError
from minx_mcp.core.memory_models import MemoryProposal, MemoryRecord
from minx_mcp.core.memory_payloads import validate_memory_payload
from minx_mcp.validation import require_non_empty

logger = logging.getLogger(__name__)

_ALLOWED_ACTORS = frozenset({"system", "detector", "user", "harness", "vault_sync"})
_ALLOWED_STATUS = frozenset({"candidate", "active", "rejected", "expired"})

REJECTED_MEMORY_TTL_DAYS = 30


def _utc_reference_iso(now: datetime | None = None) -> str:
    reference = now if now is not None else datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return reference.astimezone(UTC).isoformat()


def _raise_memory_status_conflict(memory_id: int, expected_status: str) -> None:
    raise ConflictError(
        f"memory {memory_id} status changed; expected {expected_status}, row was modified concurrently",
        data={"memory_id": memory_id, "expected_status": expected_status},
    )


class MemoryService(BaseService):
    """Persist memories and append lifecycle rows to ``memory_events``."""

    def __init__(self, db_path: Path, *, conn: Connection | None = None) -> None:
        super().__init__(db_path)
        self._external_conn: Connection | None = conn

    @property
    def conn(self) -> Connection:
        if self._external_conn is not None:
            return self._external_conn
        return super().conn

    def close(self) -> None:
        if self._external_conn is None:
            super().close()

    def create_memory(
        self,
        *,
        memory_type: str,
        scope: str,
        subject: str,
        confidence: float,
        payload: dict[str, object],
        source: str,
        reason: str = "",
        actor: str = "system",
    ) -> MemoryRecord:
        payload = validate_memory_payload(memory_type, payload)
        mt = require_non_empty("memory_type", memory_type)
        sc = require_non_empty("scope", scope)
        sj = require_non_empty("subject", subject)
        src = require_non_empty("source", source)
        _validate_confidence(confidence)
        _validate_actor(actor)
        status: str = "active" if confidence >= 0.8 else "candidate"
        return self._insert_memory_and_events(
            memory_type=mt,
            scope=sc,
            subject=sj,
            confidence=confidence,
            status=status,
            payload=payload,
            source=src,
            reason=reason,
            actor=actor,
            emit_promoted=status == "active",
        )

    def list_memories(
        self,
        *,
        status: str | None = None,
        memory_type: str | None = None,
        scope: str | None = None,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        _validate_limit(limit)
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            if status not in _ALLOWED_STATUS:
                raise InvalidInputError(f"status must be one of {sorted(_ALLOWED_STATUS)}")
            clauses.append("status = ?")
            params.append(status)
            if status == "active":
                clauses.append("(expires_at IS NULL OR expires_at > ?)")
                params.append(_utc_reference_iso(None))
        if memory_type is not None:
            clauses.append("memory_type = ?")
            params.append(require_non_empty("memory_type", memory_type))
        if scope is not None:
            clauses.append("scope = ?")
            params.append(require_non_empty("scope", scope))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM memories {where} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def get_memory(self, memory_id: int) -> MemoryRecord:
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Memory {memory_id} not found")
        return _row_to_record(row)

    def confirm_memory(self, memory_id: int, *, actor: str = "user") -> MemoryRecord:
        _validate_actor(actor)
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Memory {memory_id} not found")
        expected_status = str(row["status"])
        if expected_status != "candidate":
            raise InvalidInputError("Only candidate memories can be confirmed")
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(
                """
                UPDATE memories
                SET status = 'active',
                    last_confirmed_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ? AND status = ?
                """,
                (memory_id, expected_status),
            )
            if cur.rowcount != 1:
                if self.conn.in_transaction:
                    self.conn.rollback()
                _raise_memory_status_conflict(memory_id, expected_status)
            _insert_event(
                self.conn,
                memory_id,
                "confirmed",
                {"reason": "user confirmed candidate"},
                actor,
            )
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        return self.get_memory(memory_id)

    def reject_memory(
        self,
        memory_id: int,
        *,
        actor: str = "user",
        reason: str = "",
    ) -> MemoryRecord:
        """Reject a pending candidate.

        Only memories in the ``candidate`` status may be rejected; ``active`` memories
        have already been confirmed (explicitly or via auto-promotion) and cannot be
        demoted through this path. To remove an active memory, use :meth:`expire_memory`.
        """
        _validate_actor(actor)
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Memory {memory_id} not found")
        expected_status = str(row["status"])
        if expected_status != "candidate":
            raise InvalidInputError("Only candidate memories can be rejected")
        now_utc = datetime.now(UTC)
        expires_at_iso = (now_utc + timedelta(days=REJECTED_MEMORY_TTL_DAYS)).isoformat()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(
                """
                UPDATE memories
                SET status = 'rejected',
                    expires_at = ?,
                    updated_at = datetime('now')
                WHERE id = ? AND status = ?
                """,
                (expires_at_iso, memory_id, expected_status),
            )
            if cur.rowcount != 1:
                if self.conn.in_transaction:
                    self.conn.rollback()
                _raise_memory_status_conflict(memory_id, expected_status)
            _insert_event(self.conn, memory_id, "rejected", {"reason": reason}, actor)
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        return self.get_memory(memory_id)

    def expire_memory(
        self,
        memory_id: int,
        *,
        actor: str = "system",
        reason: str = "",
    ) -> MemoryRecord:
        """Expire an active memory.

        Only ``active`` memories may be expired through this path. Restricting the
        allowed prior statuses is a **correctness requirement**, not just a
        conservatism: if a ``rejected`` row were demoted to ``expired``, the
        ``ingest_proposals`` lookup (which branches on the **latest** row's status)
        would then treat the triple as "expired → insert fresh row" and silently
        resurrect the user's rejection. Terminal states (``rejected``, ``expired``)
        are sticky by design.

        - ``candidate``: use :meth:`reject_memory` instead.
        - ``rejected``: already terminal; no transition is allowed.
        - ``expired``: idempotent — returns the existing row unchanged.
        """
        _validate_actor(actor)
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Memory {memory_id} not found")
        status_before = str(row["status"])
        if status_before == "expired":
            return _row_to_record(row)
        if status_before != "active":
            raise InvalidInputError(
                "Only active memories can be expired "
                "(candidates should be rejected; rejected rows are terminal)"
            )
        expected_status = status_before
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(
                """
                UPDATE memories
                SET status = 'expired', updated_at = datetime('now')
                WHERE id = ? AND status = ?
                """,
                (memory_id, expected_status),
            )
            if cur.rowcount != 1:
                if self.conn.in_transaction:
                    self.conn.rollback()
                _raise_memory_status_conflict(memory_id, expected_status)
            _insert_event(self.conn, memory_id, "expired", {"reason": reason}, actor)
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        return self.get_memory(memory_id)

    def update_payload(
        self,
        memory_id: int,
        *,
        payload: dict[str, object],
        actor: str = "system",
    ) -> MemoryRecord:
        _validate_actor(actor)
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Memory {memory_id} not found")
        expected_status = str(row["status"])
        if expected_status in {"rejected", "expired"}:
            raise InvalidInputError("Cannot update payload for rejected or expired memories")
        memory_type = str(row["memory_type"])
        payload = validate_memory_payload(memory_type, payload)
        payload_json = json.dumps(payload, sort_keys=True)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(
                """
                UPDATE memories
                SET payload_json = ?, updated_at = datetime('now')
                WHERE id = ? AND status = ?
                """,
                (payload_json, memory_id, expected_status),
            )
            if cur.rowcount != 1:
                if self.conn.in_transaction:
                    self.conn.rollback()
                _raise_memory_status_conflict(memory_id, expected_status)
            # Event documents the full replacement payload (not a field-level diff).
            _insert_event(
                self.conn,
                memory_id,
                "payload_updated",
                {"payload": payload},
                actor,
            )
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        return self.get_memory(memory_id)

    def list_pending_candidates(
        self,
        *,
        scope: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        _validate_limit(limit)
        clauses = [
            "status = 'candidate'",
            "(expires_at IS NULL OR expires_at > ?)",
        ]
        params: list[object] = [_utc_reference_iso(None)]
        if scope is not None:
            clauses.append("scope = ?")
            params.append(require_non_empty("scope", scope))
        params.append(limit)
        sql = (
            "SELECT * FROM memories "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY confidence DESC, created_at ASC "
            "LIMIT ?"
        )
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def list_active_memories(
        self,
        *,
        memory_type: str | None = None,
        scope: str | None = None,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """Return active memories, excluding rows past ``expires_at`` (defensive TTL gate)."""
        return self.list_memories(
            status="active",
            memory_type=memory_type,
            scope=scope,
            limit=limit,
        )

    def prune_expired_memories(self, now: datetime | None = None) -> int:
        """Delete rejected memories whose expires_at is in the past. Returns count pruned."""
        reference_iso = _utc_reference_iso(now)
        cur = self.conn.execute(
            "DELETE FROM memories "
            "WHERE status = 'rejected' "
            "AND expires_at IS NOT NULL "
            "AND expires_at <= ?",
            (reference_iso,),
        )
        self.conn.commit()
        return int(cur.rowcount or 0)

    def ingest_proposals(
        self,
        proposals: Iterable[MemoryProposal],
        *,
        actor: str = "detector",
    ) -> list[MemoryRecord]:
        """Ingest detector proposals with dedupe, merge, and auto-promote.

        For each proposal, the most recent row for ``(memory_type, scope, subject)``
        is consulted and one of four paths is taken:

        * **Candidate or active prior**: shallow-merge payload (new keys win),
          ``confidence = max(prior, new)``, overwrite ``reason``, emit
          ``payload_updated``. If the merged confidence crosses 0.8 from candidate,
          promote to ``active`` and emit ``promoted`` after the update.
        * **Rejected prior**: the proposal is **silently suppressed** — no DB write,
          no event, and the proposal is **omitted from the returned list**. This
          honors the spec's "rejected means don't pester the user again" contract;
          detectors must not re-introduce user-rejected memories.
        * **Expired prior**: treated as no prior row. Expiry is TTL-driven, so
          re-proposing after expiry represents new evidence and a fresh lifecycle.
        * **No prior row**: insert a new memory as ``active`` if ``confidence >= 0.8``
          else ``candidate``; emit ``created`` (and ``promoted`` if auto-promoted).

        Returns the records that were created or updated, in input order. Suppressed
        (rejected-prior) proposals are excluded; callers that need to observe which
        inputs were dropped can diff by ``(memory_type, scope, subject)``.

        Concurrency note
        ----------------
        The per-proposal "latest row" ``SELECT`` still runs before the merge
        transaction's ``BEGIN IMMEDIATE``, but the merge ``UPDATE`` is guarded
        with ``WHERE id = ? AND status = ?`` (the status observed at read time).
        If another writer changes the row first (for example rejecting between
        read and write), ``rowcount`` is zero and :class:`ConflictError` is
        raised instead of merging onto the wrong lifecycle state. Migration
        015's partial unique index still guards duplicate live inserts.
        """
        _validate_actor(actor)
        out: list[MemoryRecord] = []
        for proposal in proposals:
            row = self.conn.execute(
                """
                SELECT * FROM memories
                WHERE memory_type = ? AND scope = ? AND subject = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (proposal.memory_type, proposal.scope, proposal.subject),
            ).fetchone()
            _validate_confidence(proposal.confidence)
            prior_status = str(row["status"]) if row is not None else None

            if prior_status == "rejected":
                continue

            try:
                validated_payload = validate_memory_payload(
                    proposal.memory_type, dict(proposal.payload)
                )
            except InvalidInputError:
                logger.warning(
                    "skipping memory proposal with invalid payload: memory_type=%r "
                    "scope=%r subject=%r source=%r",
                    proposal.memory_type,
                    proposal.scope,
                    proposal.subject,
                    proposal.source,
                )
                continue

            if row is None or prior_status == "expired":
                status: str = "active" if proposal.confidence >= 0.8 else "candidate"
                rec = self._insert_memory_and_events(
                    memory_type=require_non_empty("memory_type", proposal.memory_type),
                    scope=require_non_empty("scope", proposal.scope),
                    subject=require_non_empty("subject", proposal.subject),
                    confidence=proposal.confidence,
                    status=status,
                    payload=validated_payload,
                    source=require_non_empty("source", proposal.source),
                    reason=proposal.reason,
                    actor=actor,
                    emit_promoted=status == "active",
                )
                out.append(rec)
                continue

            assert prior_status is not None
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                memory_id = int(row["id"])
                prior_payload = _parse_payload_json(str(row["payload_json"]))
                merged: dict[str, object] = {**prior_payload, **validated_payload}
                new_confidence = max(float(row["confidence"]), float(proposal.confidence))
                new_status = prior_status
                promoted = False
                if new_confidence >= 0.8 and prior_status == "candidate":
                    new_status = "active"
                    promoted = True
                payload_json = json.dumps(merged, sort_keys=True)
                expected_status = prior_status
                cur = self.conn.execute(
                    """
                    UPDATE memories
                    SET confidence = ?,
                        payload_json = ?,
                        reason = ?,
                        status = ?,
                        updated_at = datetime('now')
                    WHERE id = ? AND status = ?
                    """,
                    (
                        new_confidence,
                        payload_json,
                        proposal.reason,
                        new_status,
                        memory_id,
                        expected_status,
                    ),
                )
                if cur.rowcount != 1:
                    if self.conn.in_transaction:
                        self.conn.rollback()
                    _raise_memory_status_conflict(memory_id, expected_status)
                _insert_event(
                    self.conn,
                    memory_id,
                    "payload_updated",
                    {"payload": merged, "prior_confidence": float(row["confidence"])},
                    actor,
                )
                if promoted:
                    _insert_event(self.conn, memory_id, "promoted", {}, actor)
                self.conn.commit()
            except Exception:
                if self.conn.in_transaction:
                    self.conn.rollback()
                raise
            out.append(self.get_memory(int(row["id"])))
        return out

    def _insert_memory_and_events(
        self,
        *,
        memory_type: str,
        scope: str,
        subject: str,
        confidence: float,
        status: str,
        payload: dict[str, object],
        source: str,
        reason: str,
        actor: str,
        emit_promoted: bool,
    ) -> MemoryRecord:
        payload_json = json.dumps(payload, sort_keys=True)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(
                """
                INSERT INTO memories (
                    memory_type, scope, subject, confidence, status,
                    payload_json, source, reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    memory_type,
                    scope,
                    subject,
                    confidence,
                    status,
                    payload_json,
                    source,
                    reason,
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("memories insert did not return a row id")
            memory_id = int(cur.lastrowid)
            _insert_event(self.conn, memory_id, "created", {}, actor)
            if emit_promoted:
                _insert_event(self.conn, memory_id, "promoted", {}, actor)
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            if self.conn.in_transaction:
                self.conn.rollback()
            # Migration 015's partial unique index enforces at most one live
            # (candidate/active) row per (memory_type, scope, subject). Translate
            # that specific violation into a CONFLICT error so MCP clients can
            # distinguish "duplicate live memory" from a generic internal
            # failure. Other IntegrityErrors (CHECK / NOT NULL / unrelated
            # UNIQUE) remain unmapped and surface as INTERNAL_ERROR — they
            # indicate programmer bugs, not caller-addressable conflicts.
            #
            # Detection strategy: do NOT parse SQLite's error message, which
            # names columns (not indexes) and would be fragile to future
            # schema evolution. Instead, verify against actual state — if
            # there is a live row for the proposed triple, the IntegrityError
            # is necessarily the live-triple index. This is self-verifying
            # and immune to future UNIQUE constraints that happen to share
            # column names.
            live = self.conn.execute(
                """
                SELECT 1 FROM memories
                WHERE memory_type = ? AND scope = ? AND subject = ?
                  AND status IN ('candidate', 'active')
                LIMIT 1
                """,
                (memory_type, scope, subject),
            ).fetchone()
            if live is not None:
                raise ConflictError(
                    "A live memory already exists for this (memory_type, scope, subject)",
                    data={
                        "memory_type": memory_type,
                        "scope": scope,
                        "subject": subject,
                    },
                ) from exc
            raise
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        return self.get_memory(memory_id)


def _insert_event(
    conn: Connection,
    memory_id: int,
    event_type: str,
    payload: dict[str, object],
    actor: str,
) -> None:
    conn.execute(
        """
        INSERT INTO memory_events (memory_id, event_type, payload_json, actor, created_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (memory_id, event_type, json.dumps(payload, sort_keys=True), actor),
    )


def _parse_payload_json(raw: str) -> dict[str, object]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInputError("stored payload_json is not valid JSON") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("stored payload_json must be a JSON object")
    return cast(dict[str, object], data)


def _row_to_record(row: Any) -> MemoryRecord:
    return MemoryRecord(
        id=int(row["id"]),
        memory_type=str(row["memory_type"]),
        scope=str(row["scope"]),
        subject=str(row["subject"]),
        confidence=float(row["confidence"]),
        status=str(row["status"]),
        payload=_parse_payload_json(str(row["payload_json"])),
        source=str(row["source"]),
        reason=str(row["reason"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_confirmed_at=row["last_confirmed_at"] if row["last_confirmed_at"] is not None else None,
        expires_at=row["expires_at"] if row["expires_at"] is not None else None,
    )


def _validate_actor(actor: str) -> None:
    if actor not in _ALLOWED_ACTORS:
        raise InvalidInputError(f"actor must be one of {sorted(_ALLOWED_ACTORS)}")


def _validate_confidence(confidence: float) -> None:
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise InvalidInputError("confidence must be a number")
    c = float(confidence)
    if c < 0 or c > 1:
        raise InvalidInputError("confidence must be between 0 and 1 inclusive")


def _validate_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise InvalidInputError("limit must be an integer")
    if limit < 1 or limit > 500:
        raise InvalidInputError("limit must be between 1 and 500")


def memory_record_as_dict(record: MemoryRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "memory_type": record.memory_type,
        "scope": record.scope,
        "subject": record.subject,
        "confidence": record.confidence,
        "status": record.status,
        "payload": record.payload,
        "source": record.source,
        "reason": record.reason,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "last_confirmed_at": record.last_confirmed_at,
        "expires_at": record.expires_at,
    }
