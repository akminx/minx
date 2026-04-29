"""SQLite-backed memory CRUD and proposal ingestion (Slice 6a)."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from minx_mcp.base_service import BaseService
from minx_mcp.contracts import ConflictError, InvalidInputError, NotFoundError
from minx_mcp.core.fingerprint import content_fingerprint
from minx_mcp.core.memory_models import MemoryProposal, MemoryRecord
from minx_mcp.core.memory_payloads import (
    PAYLOAD_MODELS,
    coerce_prior_payload_to_schema,
    validate_memory_payload,
)
from minx_mcp.core.memory_secret_scanning import (
    MemorySecretScanResult,
    merge_event_payload,
    raise_secret_detected,
    redaction_event_payload,
    sanitize_existing_subject,
    scan_event_reason,
    scan_memory_input,
    scan_payload_only,
)
from minx_mcp.core.secret_scanner import SecretVerdictKind, redact_secrets
from minx_mcp.validation import (
    InvalidPayloadJSONError,
    parse_payload_json,
    require_non_empty,
)

logger = logging.getLogger(__name__)

_ALLOWED_ACTORS = frozenset({"system", "detector", "user", "harness", "vault_sync"})
_ALLOWED_STATUS = frozenset({"candidate", "active", "rejected", "expired"})
_ALLOWED_EDGE_PREDICATES = frozenset({"supersedes", "contradicts", "cites"})
_ALLOWED_EDGE_DIRECTIONS = frozenset({"incoming", "outgoing", "both"})

# Confidence at or above this threshold promotes a memory from candidate -> active.
# memory_capture must stay strictly below this floor; memory_create may exceed it.
ACTIVE_CONFIDENCE_THRESHOLD = 0.8

REJECTED_MEMORY_TTL_DAYS = 30


@dataclass(frozen=True)
class MemoryProposalFailure:
    memory_type: str
    scope: str
    subject: str
    reason: str


@dataclass(frozen=True)
class MemoryProposalSuppression:
    """A proposal skipped due to a prior rejection (structural or content).

    ``reason`` is one of:
    - ``"structural_rejected_prior"`` — a row with the same
      ``(memory_type, scope, subject)`` was previously rejected.
    - ``"content_fingerprint_rejected_prior"`` — a row with the same
      content fingerprint (but different triple) was previously rejected.
    """

    memory_type: str
    scope: str
    subject: str
    reason: str


@dataclass(frozen=True)
class IngestProposalsReport:
    succeeded: list[MemoryRecord]
    failures: list[MemoryProposalFailure]
    suppressed: list[MemoryProposalSuppression]

    def __iter__(self) -> Iterator[MemoryRecord]:
        return iter(self.succeeded)

    def __len__(self) -> int:
        return len(self.succeeded)

    def __getitem__(self, index: int) -> MemoryRecord:
        return self.succeeded[index]

    def __eq__(self, other: object) -> bool:
        # Backward-compat ONLY: lets legacy callers/tests written before
        # Slice 6g compare ``report == [record, ...]`` (treating the report
        # as "the list of successfully ingested records"). For
        # report-vs-report comparisons we delegate to the dataclass-
        # generated equality so ``succeeded``/``failures``/``suppressed``
        # all participate — see ``test_ingest_proposals_report_equality_includes_suppressed``.
        if isinstance(other, list):
            return self.succeeded == other
        return super().__eq__(other)


@dataclass(frozen=True)
class MemorySearchResult:
    memory: MemoryRecord
    rank: float
    snippet: str


@dataclass(frozen=True)
class MemoryEdge:
    id: int
    source_memory_id: int
    target_memory_id: int
    predicate: str
    relation_note: str
    actor: str
    created_at: str
    updated_at: str


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


def _canonical_aliases(aliases: object) -> str:
    """Canonical JSON form of an aliases list for fingerprinting.

    Normalize each alias first, then sort, so Unicode form drift cannot
    reorder the list between two rows with the "same" aliases. Non-string
    entries are stringified via ``str()`` — in practice
    ``coerce_prior_payload_to_schema`` would have dropped them, but this
    is belt-and-suspenders against arbitrary stored content.
    """
    from minx_mcp.core.fingerprint import normalize_for_fingerprint

    if not aliases:
        return ""
    if not isinstance(aliases, list | tuple):
        return ""
    normalized = sorted(normalize_for_fingerprint(str(a)) for a in aliases)
    return json.dumps(normalized, ensure_ascii=False)


def _memory_fingerprint_input(
    memory_type: str,
    payload: dict[str, object],
    *,
    scope: str,
    subject: str,
) -> tuple[str, str, str, str, str]:
    """Return the 5-tuple (memory_type, scope, subject, note, value_part).

    ``scope`` and ``subject`` are required kwargs: none of the Pydantic
    payload models carry them — they are row/proposal attributes, not
    payload fields. Every caller has them in hand and passes them
    explicitly.

    For known types (those registered in ``PAYLOAD_MODELS``) the
    ``value_part`` slot is per-type — see the §5.2 table in the
    Slice 6g spec for the per-type mapping.

    For unknown types the fallback is ``(memory_type, scope, subject,
    "", json.dumps(payload, sort_keys=True, ensure_ascii=False))`` —
    the whole payload as canonical JSON. This is safe-but-degraded:
    dedup still works on identical duplicates, but detector refinement
    keys like ``category`` participate in the fingerprint (see §5.2
    "Degraded dedup for unknown memory types").
    """
    note = str(payload.get("note") or "")

    if memory_type == "preference":
        value_part = str(payload.get("value") or "")
    elif memory_type == "pattern":
        value_part = str(payload.get("signal") or "")
    elif memory_type == "entity_fact":
        value_part = _canonical_aliases(payload.get("aliases"))
    elif memory_type == "constraint":
        value_part = str(payload.get("limit_value") or "")
    elif memory_type in PAYLOAD_MODELS:
        # Known type that has a Pydantic model but no entry above. This
        # means a new type was added to PAYLOAD_MODELS without updating
        # this function. Per §11 rule 7, refuse to silently degrade to
        # the unknown-type JSON fallback — that would ship two
        # fingerprint variants for the same logical content.
        raise RuntimeError(
            f"_memory_fingerprint_input missing per-type mapping for "
            f"registered memory_type={memory_type!r}; update the "
            f"function to add it (see Slice 6g spec §5.2)"
        )
    else:
        note = ""
        value_part = json.dumps(payload, sort_keys=True, ensure_ascii=False)

    return (memory_type, scope, subject, note, value_part)


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
        mt = require_non_empty("memory_type", memory_type)
        sc = require_non_empty("scope", scope)
        sj = require_non_empty("subject", subject)
        src = require_non_empty("source", source)
        _validate_confidence(confidence)
        _validate_actor(actor)
        raw_scan = scan_memory_input(
            memory_type=mt,
            scope=sc,
            subject=sj,
            payload=dict(payload),
            source=src,
            reason=reason,
            scan_payload_values=False,
        )
        if raw_scan.verdict is SecretVerdictKind.BLOCK:
            raise_secret_detected(raw_scan)
        payload = validate_memory_payload(mt, raw_scan.payload)
        validated_scan = scan_memory_input(
            memory_type=raw_scan.memory_type,
            scope=raw_scan.scope,
            subject=raw_scan.subject,
            payload=payload,
            source=raw_scan.source,
            reason=raw_scan.reason,
        )
        if validated_scan.verdict is SecretVerdictKind.BLOCK:
            raise_secret_detected(validated_scan)
        status: str = "active" if confidence >= ACTIVE_CONFIDENCE_THRESHOLD else "candidate"
        fp = content_fingerprint(
            *_memory_fingerprint_input(
                validated_scan.memory_type,
                validated_scan.payload,
                scope=validated_scan.scope,
                subject=validated_scan.subject,
            )
        )
        return self._insert_memory_and_events(
            memory_type=validated_scan.memory_type,
            scope=validated_scan.scope,
            subject=validated_scan.subject,
            confidence=confidence,
            status=status,
            payload=validated_scan.payload,
            source=validated_scan.source,
            reason=validated_scan.reason,
            actor=actor,
            emit_promoted=status == "active",
            fingerprint=fp,
            created_event_payload=redaction_event_payload(raw_scan, validated_scan),
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
        if status != "expired":
            clauses.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(_utc_reference_iso(None))
        if memory_type is not None:
            clauses.append("memory_type = ?")
            params.append(require_non_empty("memory_type", memory_type))
        if scope is not None:
            clauses.append("scope = ?")
            params.append(require_non_empty("scope", scope))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # Safe: WHERE is only AND of literal fragments with ?; filter values are bound in params.
        sql = f"SELECT * FROM memories {where} ORDER BY id DESC LIMIT ?"  # noqa: S608
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def search_memories(
        self,
        *,
        query: str,
        scope: str | None = None,
        memory_type: str | None = None,
        status: str | None = "active",
        limit: int = 25,
    ) -> list[MemorySearchResult]:
        cleaned_query = require_non_empty("query", query)
        _validate_search_limit(limit)
        _validate_fts_query_syntax(self.conn, cleaned_query)
        clauses = ["memory_fts MATCH ?"]
        params: list[object] = [cleaned_query]
        if status is not None:
            if status not in _ALLOWED_STATUS:
                raise InvalidInputError(f"status must be one of {sorted(_ALLOWED_STATUS)}")
            clauses.append("memories.status = ?")
            params.append(status)
        if status != "expired":
            clauses.append("(memories.expires_at IS NULL OR memories.expires_at > ?)")
            params.append(_utc_reference_iso(None))
        if memory_type is not None:
            clauses.append("memories.memory_type = ?")
            params.append(require_non_empty("memory_type", memory_type))
        if scope is not None:
            clauses.append("memories.scope = ?")
            params.append(require_non_empty("scope", scope))
        sql = f"""
            SELECT
                memories.*,
                bm25(memory_fts) AS search_rank,
                snippet(memory_fts, -1, '<mark>', '</mark>', '...', 12) AS search_snippet
            FROM memory_fts
            JOIN memories ON memories.id = memory_fts.rowid
            WHERE {' AND '.join(clauses)}
            ORDER BY search_rank ASC, memories.updated_at DESC, memories.id DESC
            LIMIT ?
        """  # noqa: S608 - clauses are static fragments; all values are bound params.
        params.append(limit)
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if _is_fts_query_syntax_error(exc):
                raise InvalidInputError("query is not valid FTS5 syntax") from exc
            raise
        results: list[MemorySearchResult] = []
        for row in rows:
            try:
                memory = _row_to_record(row)
            except InvalidPayloadJSONError as exc:
                logger.warning(
                    "skipping memory FTS hit with corrupt payload_json",
                    extra={"memory_id": exc.source_id, "label": exc.label},
                )
                continue
            results.append(
                MemorySearchResult(
                    memory=memory,
                    rank=float(row["search_rank"]),
                    snippet=str(row["search_snippet"] or ""),
                )
            )
        return results

    def create_memory_edge(
        self,
        *,
        source_memory_id: int,
        target_memory_id: int,
        predicate: str,
        relation_note: str = "",
        actor: str = "system",
    ) -> MemoryEdge:
        _validate_actor(actor)
        source_id = _validate_positive_int("source_memory_id", source_memory_id)
        target_id = _validate_positive_int("target_memory_id", target_memory_id)
        if source_id == target_id:
            raise InvalidInputError("source_memory_id and target_memory_id must differ")
        pred = _validate_edge_predicate(predicate)
        note = _scan_edge_relation_note(relation_note)
        self._require_memory_exists(source_id)
        self._require_memory_exists(target_id)
        try:
            cur = self.conn.execute(
                """
                INSERT INTO memory_edges (
                    source_memory_id, target_memory_id, predicate, relation_note, actor
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (source_id, target_id, pred, note, actor),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            raise ConflictError(
                "memory edge already exists",
                data={
                    "conflict_kind": "memory_edge",
                    "source_memory_id": source_id,
                    "target_memory_id": target_id,
                    "predicate": pred,
                },
            ) from exc
        if cur.lastrowid is None:
            raise RuntimeError("memory edge insert did not return a row id")
        edge_id = int(cur.lastrowid)
        edge = self.get_memory_edge(edge_id)
        if edge is None:
            raise RuntimeError("memory edge insert did not return a readable row")
        return edge

    def get_memory_edge(self, edge_id: int) -> MemoryEdge | None:
        eid = _validate_positive_int("edge_id", edge_id)
        row = self.conn.execute("SELECT * FROM memory_edges WHERE id = ?", (eid,)).fetchone()
        if row is None:
            return None
        return _row_to_edge(row)

    def list_memory_edges(
        self,
        memory_id: int,
        *,
        direction: str = "both",
        predicate: str | None = None,
        limit: int = 100,
    ) -> list[MemoryEdge]:
        mid = _validate_positive_int("memory_id", memory_id)
        _validate_edge_direction(direction)
        _validate_search_limit(limit)
        clauses: list[str] = []
        params: list[object] = []
        if direction == "incoming":
            clauses.append("target_memory_id = ?")
            params.append(mid)
        elif direction == "outgoing":
            clauses.append("source_memory_id = ?")
            params.append(mid)
        else:
            clauses.append("(source_memory_id = ? OR target_memory_id = ?)")
            params.extend((mid, mid))
        if predicate is not None:
            clauses.append("predicate = ?")
            params.append(_validate_edge_predicate(predicate))
        sql = f"""
            SELECT *
            FROM memory_edges
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            LIMIT ?
        """  # noqa: S608 - clauses are fixed fragments; values are bound params.
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_edge(row) for row in rows]

    def delete_memory_edge(self, edge_id: int) -> bool:
        eid = _validate_positive_int("edge_id", edge_id)
        cur = self.conn.execute("DELETE FROM memory_edges WHERE id = ?", (eid,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_memory(self, memory_id: int) -> MemoryRecord:
        row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Memory {memory_id} not found")
        return _row_to_record(row)

    def _require_memory_exists(self, memory_id: int) -> None:
        row = self.conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Memory {memory_id} not found")

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
            safe_reason, redaction_payload = scan_event_reason(reason)
            event_payload: dict[str, object] = {"reason": safe_reason}
            if redaction_payload is not None:
                event_payload = {**event_payload, **redaction_payload}
            _insert_event(self.conn, memory_id, "rejected", event_payload, actor)
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
            safe_reason, redaction_payload = scan_event_reason(reason)
            event_payload: dict[str, object] = {"reason": safe_reason}
            if redaction_payload is not None:
                event_payload = {**event_payload, **redaction_payload}
            _insert_event(self.conn, memory_id, "expired", event_payload, actor)
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
        raw_scan = scan_payload_only(dict(payload), scan_payload_values=False)
        if raw_scan.verdict is SecretVerdictKind.BLOCK:
            raise_secret_detected(raw_scan)
        payload = validate_memory_payload(memory_type, raw_scan.payload)
        validated_scan = scan_payload_only(payload)
        if validated_scan.verdict is SecretVerdictKind.BLOCK:
            raise_secret_detected(validated_scan)
        payload_json = json.dumps(validated_scan.payload, sort_keys=True)
        # Slice 6g: recompute fingerprint over the new payload. The row's
        # (memory_type, scope, subject) do not change on update_payload,
        # so they pass through from the existing row.
        fp = content_fingerprint(
            *_memory_fingerprint_input(
                memory_type,
                validated_scan.payload,
                scope=str(row["scope"]),
                subject=str(row["subject"]),
            )
        )
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(
                """
                UPDATE memories
                SET payload_json = ?,
                    content_fingerprint = ?,
                    updated_at = datetime('now')
                WHERE id = ? AND status = ?
                """,
                (payload_json, fp, memory_id, expected_status),
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
                merge_event_payload({"payload": validated_scan.payload}, raw_scan, validated_scan),
                actor,
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            if self.conn.in_transaction:
                self.conn.rollback()
            # Slice 6g content-fingerprint partial unique index collided:
            # another live row (different from the one we're updating)
            # already holds this fingerprint. State-based probe: same
            # pattern as _insert_memory_and_events.
            blocking = self.conn.execute(
                """
                SELECT id FROM memories
                WHERE content_fingerprint = ?
                  AND status IN ('candidate', 'active')
                  AND id != ?
                LIMIT 1
                """,
                (fp, memory_id),
            ).fetchone()
            if blocking is not None:
                raise ConflictError(
                    "Updating this memory's payload would duplicate another live memory's content",
                    data={
                        "conflict_kind": "content_fingerprint_update",
                        "memory_id": memory_id,
                        "blocking_memory_id": int(blocking["id"]),
                    },
                ) from exc
            # Unexpected IntegrityError — re-raise as INTERNAL_ERROR at
            # the MCP boundary.
            raise
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
        # Safe: WHERE is AND of fixed SQL snippets and ? placeholders; scope/limit values are bound.
        where_sql = f"WHERE {' AND '.join(clauses)} "
        sql = (
            "SELECT * FROM memories "  # noqa: S608
            + where_sql
            + "ORDER BY confidence DESC, created_at ASC "
            + "LIMIT ?"
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
        """Delete rejected memories whose expires_at is in the past. Returns count pruned.

        Callers are responsible for committing the surrounding transaction.
        This method does NOT call conn.commit() so it can be composed safely inside
        an outer transaction (scoped_connection pattern).
        """
        reference_iso = _utc_reference_iso(now)
        cur = self.conn.execute(
            "DELETE FROM memories "
            "WHERE status = 'rejected' "
            "AND expires_at IS NOT NULL "
            "AND expires_at <= ?",
            (reference_iso,),
        )
        return int(cur.rowcount or 0)

    def ingest_proposals(
        self,
        proposals: Iterable[MemoryProposal],
        *,
        actor: str = "detector",
    ) -> IngestProposalsReport:
        """Ingest detector proposals with dedupe, merge, auto-promote, and
        content-equivalence dedup (Slice 6g).

        For each proposal the flow is:

        1. Structural lookup on ``(memory_type, scope, subject)``.
        2. If the structural prior row is ``rejected``: append a
           :class:`MemoryProposalSuppression` with
           ``reason="structural_rejected_prior"`` to the returned
           report's ``suppressed`` list, skip. An info-level log is
           emitted at snapshot layer; this is not a warning because
           suppression is the spec's "don't pester the user again"
           contract working as intended.
        3. Validate the proposal's payload (Pydantic); invalid payloads
           record a :class:`MemoryProposalFailure`.
        4. Compute the proposal's content fingerprint over the
           validated payload (§5.2 5-tuple).
        5. Fingerprint lookup (ordered by live-first, then id desc).
        6. Dispatch on the fingerprint lookup:

           * No match → fall through to the insert/merge fork.
           * Top match is a ``rejected`` row → record a
             :class:`MemoryProposalSuppression` with
             ``reason="content_fingerprint_rejected_prior"``, skip.
           * Top match is an ``expired`` row → fall through (the
             partial unique index permits reinsertion; see migration
             020).
           * Top match is live (``candidate``/``active``) AND shares
             the proposal's triple → fall through to the existing
             in-place merge (steps 7b).
           * Top match is live AND has a different triple → execute
             the **content-equivalence merge** on the matched row
             (§7.2.3). The proposal's triple is not inserted; instead
             the matched row gains the proposal's payload shallow-
             merged in, its confidence bumped if higher, and a
             ``payload_updated`` event carrying
             ``merge_trigger="content_fingerprint"`` and the
             ``prior_identity`` of the proposal.

        7. Insert/merge fork:

           * No prior row (``row is None`` or prior status is
             ``expired``) → insert a fresh memory row via
             :meth:`_insert_memory_and_events`, passing the step-4
             fingerprint through.
           * Prior row exists with a live status on the same triple →
             in-place merge: shallow-merge payload (new keys win),
             ``confidence = max(prior, new)``, recompute fingerprint
             over the merged payload, update ``reason``, and emit
             ``payload_updated`` (and ``promoted`` if auto-promoted).

        Concurrency note
        ----------------
        Writes are serialized by ``BEGIN IMMEDIATE``. Reads happen
        before the transaction, but every write is guarded by
        ``WHERE id = ? AND status = ?`` against the status observed
        at read time — if another writer flips the row first,
        ``rowcount`` is zero and :class:`ConflictError` is raised.
        """
        _validate_actor(actor)
        out: list[MemoryRecord] = []
        failures: list[MemoryProposalFailure] = []
        suppressed: list[MemoryProposalSuppression] = []
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
            raw_scan = scan_memory_input(
                memory_type=proposal.memory_type,
                scope=proposal.scope,
                subject=proposal.subject,
                payload=dict(proposal.payload),
                source=proposal.source,
                reason=proposal.reason,
            scan_payload_values=False,
            )
            if raw_scan.verdict is SecretVerdictKind.BLOCK:
                logger.warning(
                    "skipping memory proposal with secret-detected input: kinds=%s",
                    ",".join(raw_scan.detected_kinds),
                )
                failures.append(
                    MemoryProposalFailure(
                        memory_type=raw_scan.memory_type,
                        scope=raw_scan.scope,
                        subject=raw_scan.subject,
                        reason="secret_detected",
                    )
                )
                continue
            try:
                _validate_confidence(proposal.confidence)
            except InvalidInputError:
                logger.warning(
                    "skipping memory proposal with invalid confidence: memory_type=%r scope=%r subject=%r source=%r",
                    raw_scan.memory_type,
                    raw_scan.scope,
                    raw_scan.subject,
                    raw_scan.source,
                )
                failures.append(
                    MemoryProposalFailure(
                        memory_type=raw_scan.memory_type,
                        scope=raw_scan.scope,
                        subject=raw_scan.subject,
                        reason="invalid_confidence",
                    )
                )
                continue
            prior_status = str(row["status"]) if row is not None else None

            # Rejected-structural-prior: suppress before we even validate
            # the payload. Preserves the existing "don't fail a
            # rejected-subject proposal, just drop it" contract.
            if prior_status == "rejected":
                suppressed.append(
                    MemoryProposalSuppression(
                        memory_type=raw_scan.memory_type,
                        scope=raw_scan.scope,
                        subject=raw_scan.subject,
                        reason="structural_rejected_prior",
                    )
                )
                continue

            try:
                validated_payload = validate_memory_payload(
                    raw_scan.memory_type, raw_scan.payload
                )
            except InvalidInputError:
                logger.warning(
                    "skipping memory proposal with invalid payload: memory_type=%r "
                    "scope=%r subject=%r source=%r",
                    raw_scan.memory_type,
                    raw_scan.scope,
                    raw_scan.subject,
                    raw_scan.source,
                )
                failures.append(
                    MemoryProposalFailure(
                        memory_type=raw_scan.memory_type,
                        scope=raw_scan.scope,
                        subject=raw_scan.subject,
                        reason="invalid_payload",
                    )
                )
                continue
            validated_scan = scan_memory_input(
                memory_type=raw_scan.memory_type,
                scope=raw_scan.scope,
                subject=raw_scan.subject,
                payload=validated_payload,
                source=raw_scan.source,
                reason=raw_scan.reason,
            )
            if validated_scan.verdict is SecretVerdictKind.BLOCK:
                logger.warning(
                    "skipping memory proposal with secret-detected payload: kinds=%s",
                    ",".join(validated_scan.detected_kinds),
                )
                failures.append(
                    MemoryProposalFailure(
                        memory_type=validated_scan.memory_type,
                        scope=validated_scan.scope,
                        subject=validated_scan.subject,
                        reason="secret_detected",
                    )
                )
                continue
            safe_proposal = MemoryProposal(
                memory_type=validated_scan.memory_type,
                scope=validated_scan.scope,
                subject=validated_scan.subject,
                confidence=proposal.confidence,
                payload=validated_scan.payload,
                source=validated_scan.source,
                reason=validated_scan.reason,
            )

            # Slice 6g: compute fingerprint over the validated payload;
            # scope and subject come from the proposal (never the
            # payload — see _memory_fingerprint_input docstring).
            fp = content_fingerprint(
                *_memory_fingerprint_input(
                    safe_proposal.memory_type,
                    validated_scan.payload,
                    scope=safe_proposal.scope,
                    subject=safe_proposal.subject,
                )
            )

            # Fingerprint lookup. ORDER BY CASE prefers live rows over
            # terminal rows for the same fingerprint, so the decision
            # reflects the current-live row when one exists.
            fp_match = self.conn.execute(
                """
                SELECT id, status, memory_type, scope, subject,
                       payload_json, confidence, reason
                FROM memories
                WHERE content_fingerprint = ?
                ORDER BY
                  CASE status
                    WHEN 'active' THEN 0
                    WHEN 'candidate' THEN 1
                    WHEN 'rejected' THEN 2
                    WHEN 'expired' THEN 3
                    ELSE 4
                  END,
                  id DESC
                LIMIT 1
                """,
                (fp,),
            ).fetchone()

            fp_match_status = (
                str(fp_match["status"]) if fp_match is not None else None
            )
            fp_match_same_triple = (
                fp_match is not None
                and str(fp_match["memory_type"]) == safe_proposal.memory_type
                and str(fp_match["scope"]) == safe_proposal.scope
                and str(fp_match["subject"]) == safe_proposal.subject
            )

            # Fingerprint-rejected-prior: the content already got a "no"
            # from the user on a different (or same) triple.
            if fp_match_status == "rejected":
                suppressed.append(
                    MemoryProposalSuppression(
                        memory_type=safe_proposal.memory_type,
                        scope=safe_proposal.scope,
                        subject=safe_proposal.subject,
                        reason="content_fingerprint_rejected_prior",
                    )
                )
                continue

            # Content-equivalence merge: a live row with a DIFFERENT
            # triple fingerprint-matches the proposal. The matched row
            # is the one we update; the proposal's own structural prior
            # (if any) is left untouched — the invariant note in the
            # spec (§7.2.2 step 6) explains why this case is only
            # reachable via backfill/corruption edge paths.
            if (
                fp_match is not None
                and fp_match_status in ("candidate", "active")
                and not fp_match_same_triple
            ):
                try:
                    rec = self._content_equivalence_merge(
                        fp_match=fp_match,
                        proposal=safe_proposal,
                        validated_payload=validated_scan.payload,
                        actor=actor,
                        stored_fingerprint=fp,
                        secret_scan_results=(raw_scan, validated_scan),
                    )
                except InvalidInputError as exc:
                    if not _is_secret_detected_error(exc):
                        raise
                    failures.append(
                        MemoryProposalFailure(
                            memory_type=safe_proposal.memory_type,
                            scope=safe_proposal.scope,
                            subject=safe_proposal.subject,
                            reason="secret_detected",
                        )
                    )
                    continue
                out.append(rec)
                continue

            # Insert/merge fork.
            if row is None or prior_status == "expired":
                status: str = (
                    "active"
                    if proposal.confidence >= ACTIVE_CONFIDENCE_THRESHOLD
                    else "candidate"
                )
                rec = self._insert_memory_and_events(
                    memory_type=require_non_empty("memory_type", safe_proposal.memory_type),
                    scope=require_non_empty("scope", safe_proposal.scope),
                    subject=require_non_empty("subject", safe_proposal.subject),
                    confidence=proposal.confidence,
                    status=status,
                    payload=validated_scan.payload,
                    source=require_non_empty("source", safe_proposal.source),
                    reason=safe_proposal.reason,
                    actor=actor,
                    emit_promoted=status == "active",
                    fingerprint=fp,
                    created_event_payload=redaction_event_payload(raw_scan, validated_scan),
                )
                out.append(rec)
                continue

            if prior_status is None:
                raise RuntimeError(
                    "internal: prior_status required after insert branch"
                )
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                memory_id = int(row["id"])
                prior_payload = _parse_payload_json(str(row["payload_json"]))
                # Normalize the prior payload against the current schema so
                # legacy/pre-schema rows don't carry unknown keys forward
                # through merges (they'd otherwise compound on every merge,
                # since we only validate the NEW side). For canonical
                # memory_types this drops unknown keys; for unknown types
                # it's a no-op.
                prior_payload_clean = coerce_prior_payload_to_schema(
                    safe_proposal.memory_type, prior_payload
                )
                merged: dict[str, object] = {**prior_payload_clean, **validated_scan.payload}
                merged_scan = scan_payload_only(merged)
                if merged_scan.verdict is SecretVerdictKind.BLOCK:
                    if self.conn.in_transaction:
                        self.conn.rollback()
                    failures.append(
                        MemoryProposalFailure(
                            memory_type=safe_proposal.memory_type,
                            scope=safe_proposal.scope,
                            subject=safe_proposal.subject,
                            reason="secret_detected",
                        )
                    )
                    continue
                merged = merged_scan.payload
                new_confidence = max(float(row["confidence"]), float(proposal.confidence))
                new_status = prior_status
                promoted = False
                if (
                    new_confidence >= ACTIVE_CONFIDENCE_THRESHOLD
                    and prior_status == "candidate"
                ):
                    new_status = "active"
                    promoted = True
                payload_json = json.dumps(merged, sort_keys=True)
                stored_payload_json = json.dumps(prior_payload, sort_keys=True)
                if (
                    payload_json == stored_payload_json
                    and new_confidence == float(row["confidence"])
                    and new_status == prior_status
                    and safe_proposal.reason == str(row["reason"])
                ):
                    self.conn.commit()
                    out.append(self.get_memory(int(row["id"])))
                    continue
                # Slice 6g: shallow-merge can produce a payload whose
                # fingerprint differs from both the stored row's and
                # the proposal's. Recompute over the merged payload so
                # the column never goes stale on a merged row.
                merged_fp = content_fingerprint(
                    *_memory_fingerprint_input(
                        safe_proposal.memory_type,
                        merged,
                        scope=safe_proposal.scope,
                        subject=safe_proposal.subject,
                    )
                )
                expected_status = prior_status
                cur = self.conn.execute(
                    """
                    UPDATE memories
                    SET confidence = ?,
                        payload_json = ?,
                        reason = ?,
                        status = ?,
                        content_fingerprint = ?,
                        updated_at = datetime('now')
                    WHERE id = ? AND status = ?
                    """,
                    (
                        new_confidence,
                        payload_json,
                        safe_proposal.reason,
                        new_status,
                        merged_fp,
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
                    merge_event_payload(
                        {"payload": merged, "prior_confidence": float(row["confidence"])},
                        raw_scan,
                        validated_scan,
                        merged_scan,
                    ),
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
        return IngestProposalsReport(
            succeeded=out, failures=failures, suppressed=suppressed
        )

    def _content_equivalence_merge(
        self,
        *,
        fp_match: Any,
        proposal: MemoryProposal,
        validated_payload: dict[str, object],
        actor: str,
        stored_fingerprint: str,
        secret_scan_results: tuple[MemorySecretScanResult, ...] = (),
    ) -> MemoryRecord:
        """Slice 6g content-equivalence merge.

        Runs when the fingerprint lookup in :meth:`ingest_proposals`
        finds a live row whose ``(memory_type, scope, subject)`` is
        **different** from the proposal's. Inherits the existing merge
        semantics (``max`` confidence, candidate→active promotion,
        ``promoted`` event emission, skip-write short-circuit, reason
        overwrite, ``BEGIN IMMEDIATE`` + rowcount guarding) and adds
        two deltas:

        1. UPDATE targets the fingerprint-matched row's id, not the
           proposal's triple.
        2. ``payload_updated`` event body gains ``merge_trigger`` and
           ``prior_identity`` so investigators can explain the update.

        The ``content_fingerprint`` column is not recomputed in the
        UPDATE: by definition of reaching this branch, the merged
        payload fingerprints identically to the stored row. Keeping
        the existing fingerprint value in place is the correctness
        invariant.
        """
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            memory_id = int(fp_match["id"])
            prior_payload = _parse_payload_json(str(fp_match["payload_json"]))
            prior_payload_clean = coerce_prior_payload_to_schema(
                proposal.memory_type, prior_payload
            )
            merged: dict[str, object] = {**prior_payload_clean, **validated_payload}
            merged_scan = scan_payload_only(merged)
            if merged_scan.verdict is SecretVerdictKind.BLOCK:
                raise_secret_detected(merged_scan)
            merged = merged_scan.payload
            new_confidence = max(
                float(fp_match["confidence"]), float(proposal.confidence)
            )
            prior_status = str(fp_match["status"])
            new_status = prior_status
            promoted = False
            if new_confidence >= 0.8 and prior_status == "candidate":
                new_status = "active"
                promoted = True

            payload_json = json.dumps(merged, sort_keys=True)
            stored_payload_json = json.dumps(prior_payload, sort_keys=True)
            if (
                payload_json == stored_payload_json
                and new_confidence == float(fp_match["confidence"])
                and new_status == prior_status
                and proposal.reason == str(fp_match["reason"])
            ):
                self.conn.commit()
                return self.get_memory(memory_id)

            # Hardening: verify the documented invariant that the merged
            # payload fingerprints to the same value as the already-stored
            # row. We reached this branch via a fingerprint lookup, so the
            # hashes *must* agree. But the fingerprint is computed over the
            # *merged* payload (prior merged with validated), not the stored one, so
            # a semantic drift in ``coerce_prior_payload_to_schema`` could
            # introduce a new 5-tuple part that shifts the hash, which
            # would silently leave the row with a stale ``content_fingerprint``.
            # The ``if __debug__:`` guard lets the Python bytecode compiler
            # strip the entire block under ``python -O``, so this costs
            # nothing in production.
            if __debug__:
                merged_fp = content_fingerprint(
                    *_memory_fingerprint_input(
                        proposal.memory_type,
                        merged,
                        scope=str(fp_match["scope"]),
                        subject=str(fp_match["subject"]),
                    )
                )
                if merged_fp != stored_fingerprint:
                    raise RuntimeError(
                        "content-equivalence merge invariant violated: "
                        f"merged payload fingerprint {merged_fp!r} differs "
                        f"from lookup fingerprint {stored_fingerprint!r} on "
                        f"row {memory_id}. This should be unreachable; a "
                        "mismatch implies coerce_prior_payload_to_schema "
                        "changed shape between the lookup and the merge."
                    )

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
                merge_event_payload(
                    {
                        "payload": merged,
                        "prior_confidence": float(fp_match["confidence"]),
                        "merge_trigger": "content_fingerprint",
                        "prior_identity": {
                            "memory_type": proposal.memory_type,
                            "scope": proposal.scope,
                            "subject": proposal.subject,
                        },
                    },
                    *secret_scan_results,
                    merged_scan,
                ),
                actor,
            )
            if promoted:
                _insert_event(self.conn, memory_id, "promoted", {}, actor)
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        return self.get_memory(memory_id)

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
        fingerprint: str,
        created_event_payload: dict[str, object] | None = None,
    ) -> MemoryRecord:
        payload_json = json.dumps(payload, sort_keys=True)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = self.conn.execute(
                """
                INSERT INTO memories (
                    memory_type, scope, subject, confidence, status,
                    payload_json, source, reason, content_fingerprint,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
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
                    fingerprint,
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("memories insert did not return a row id")
            memory_id = int(cur.lastrowid)
            _insert_event(self.conn, memory_id, "created", created_event_payload or {}, actor)
            if emit_promoted:
                _insert_event(self.conn, memory_id, "promoted", {}, actor)
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            if self.conn.in_transaction:
                self.conn.rollback()
            # Migration 015's partial unique index enforces at most one live
            # (candidate/active) row per (memory_type, scope, subject); Slice
            # 6g's partial unique index on content_fingerprint enforces "at
            # most one live row per equivalence class" across triples.
            # Discriminate which one fired using state-based probes — never
            # parse SQLite's error message, which names columns (not indexes)
            # and would be fragile to future schema evolution.
            #
            # Step 1: structural-triple live-row check.
            live = self.conn.execute(
                """
                SELECT id FROM memories
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
                        "conflict_kind": "structural_triple",
                        "memory_id": int(live["id"]),
                        "memory_type": memory_type,
                        "scope": scope,
                        "subject": subject,
                    },
                ) from exc
            # Step 2: content-fingerprint live-row check.
            fp_match = self.conn.execute(
                """
                SELECT id, subject FROM memories
                WHERE content_fingerprint = ?
                  AND status IN ('candidate', 'active')
                LIMIT 1
                """,
                (fingerprint,),
            ).fetchone()
            if fp_match is not None:
                raise ConflictError(
                    "A live memory with equivalent content already exists",
                    data={
                        "conflict_kind": "content_fingerprint",
                        "memory_id": int(fp_match["id"]),
                        "memory_type": memory_type,
                        "scope": scope,
                        "subject": subject,
                        "existing_subject": sanitize_existing_subject(str(fp_match["subject"])),
                    },
                ) from exc
            # Unknown violation — re-raise so it surfaces as INTERNAL_ERROR
            # at the MCP boundary. Other IntegrityErrors (CHECK / NOT NULL
            # / unrelated UNIQUE) indicate programmer bugs, not caller-
            # addressable conflicts.
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


def _parse_payload_json(raw: str, *, source_id: int | None = None) -> dict[str, object]:
    return parse_payload_json(raw, label="memory", source_id=source_id)


def _is_secret_detected_error(exc: InvalidInputError) -> bool:
    data = exc.data
    return isinstance(data, dict) and data.get("kind") == "secret_detected"


def _is_fts_query_syntax_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return (
        "fts5: syntax error" in message
        or "unterminated string" in message
        or "malformed match expression" in message
    )


def _validate_fts_query_syntax(conn: Connection, query: str) -> None:
    try:
        conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ? LIMIT 1",
            (query,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if _is_fts_query_syntax_error(exc):
            raise InvalidInputError("query is not valid FTS5 syntax") from exc
        raise


def _row_to_record(row: Any) -> MemoryRecord:
    return MemoryRecord(
        id=int(row["id"]),
        memory_type=str(row["memory_type"]),
        scope=str(row["scope"]),
        subject=str(row["subject"]),
        confidence=float(row["confidence"]),
        status=str(row["status"]),
        payload=_parse_payload_json(str(row["payload_json"]), source_id=int(row["id"])),
        source=str(row["source"]),
        reason=str(row["reason"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_confirmed_at=row["last_confirmed_at"] if row["last_confirmed_at"] is not None else None,
        expires_at=row["expires_at"] if row["expires_at"] is not None else None,
    )


def _row_to_edge(row: Any) -> MemoryEdge:
    return MemoryEdge(
        id=int(row["id"]),
        source_memory_id=int(row["source_memory_id"]),
        target_memory_id=int(row["target_memory_id"]),
        predicate=str(row["predicate"]),
        relation_note=str(row["relation_note"]),
        actor=str(row["actor"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
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


def _validate_search_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise InvalidInputError("limit must be an integer")
    if limit < 1 or limit > 100:
        raise InvalidInputError("limit must be between 1 and 100")


def _validate_positive_int(field: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError(f"{field} must be an integer")
    if value < 1:
        raise InvalidInputError(f"{field} must be positive")
    return value


def _validate_edge_predicate(predicate: str) -> str:
    pred = require_non_empty("predicate", predicate)
    if pred not in _ALLOWED_EDGE_PREDICATES:
        raise InvalidInputError(f"predicate must be one of {sorted(_ALLOWED_EDGE_PREDICATES)}")
    return pred


def _validate_edge_direction(direction: str) -> None:
    if direction not in _ALLOWED_EDGE_DIRECTIONS:
        raise InvalidInputError(f"direction must be one of {sorted(_ALLOWED_EDGE_DIRECTIONS)}")


def _scan_edge_relation_note(note: str) -> str:
    verdict = redact_secrets(note)
    if verdict.verdict is SecretVerdictKind.BLOCK:
        raise InvalidInputError(
            "Secret detected in memory edge input",
            data={
                "kind": "secret_detected",
                "verdict": "block",
                "surface": "memory_graph",
                "detected_kinds": sorted({finding.kind for finding in verdict.findings}),
                "locations": [
                    {"field": "relation_note", "start": finding.start, "end": finding.end}
                    for finding in verdict.findings
                ],
            },
        )
    return verdict.text


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


def memory_edge_as_dict(edge: MemoryEdge) -> dict[str, object]:
    return {
        "id": edge.id,
        "source_memory_id": edge.source_memory_id,
        "target_memory_id": edge.target_memory_id,
        "predicate": edge.predicate,
        "relation_note": edge.relation_note,
        "actor": edge.actor,
        "created_at": edge.created_at,
        "updated_at": edge.updated_at,
    }
