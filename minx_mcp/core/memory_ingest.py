"""Detector proposal ingestion for durable memory."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, cast

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.fingerprint import content_fingerprint
from minx_mcp.core.memory_models import MemoryProposal, MemoryRecord
from minx_mcp.core.memory_payloads import coerce_prior_payload_to_schema, validate_memory_payload
from minx_mcp.core.memory_secret_scanning import (
    MemorySecretScanResult,
    merge_event_payload,
    raise_secret_detected,
    redaction_event_payload,
    scan_memory_input,
    scan_payload_only,
)
from minx_mcp.core.secret_scanner import SecretVerdictKind
from minx_mcp.validation import require_non_empty

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemoryProposalFailure:
    memory_type: str
    scope: str
    subject: str
    reason: str


@dataclass(frozen=True)
class MemoryProposalSuppression:
    """A proposal skipped due to a prior rejection."""

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
        if isinstance(other, list):
            return self.succeeded == other
        return super().__eq__(other)


def ingest_proposals(
    service: Any,
    proposals: Iterable[MemoryProposal],
    *,
    actor: str,
    validate_actor: Callable[[str], None],
    validate_confidence: Callable[[float], None],
    memory_fingerprint_input: Callable[..., tuple[str, str, str, str, str]],
    insert_event: Callable[..., None],
    raise_memory_status_conflict: Callable[[int, str], None],
    parse_payload_json: Callable[[str], dict[str, object]],
    is_secret_detected_error: Callable[[InvalidInputError], bool],
    active_confidence_threshold: float,
) -> IngestProposalsReport:
    validate_actor(actor)
    out: list[MemoryRecord] = []
    failures: list[MemoryProposalFailure] = []
    suppressed: list[MemoryProposalSuppression] = []
    for proposal in proposals:
        row = service.conn.execute(
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
            validate_confidence(proposal.confidence)
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
            validated_payload = validate_memory_payload(raw_scan.memory_type, raw_scan.payload)
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

        fp = content_fingerprint(
            *memory_fingerprint_input(
                safe_proposal.memory_type,
                validated_scan.payload,
                scope=safe_proposal.scope,
                subject=safe_proposal.subject,
            )
        )

        fp_match = service.conn.execute(
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

        fp_match_status = str(fp_match["status"]) if fp_match is not None else None
        fp_match_same_triple = (
            fp_match is not None
            and str(fp_match["memory_type"]) == safe_proposal.memory_type
            and str(fp_match["scope"]) == safe_proposal.scope
            and str(fp_match["subject"]) == safe_proposal.subject
        )

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

        if fp_match is not None and fp_match_status in ("candidate", "active") and not fp_match_same_triple:
            try:
                rec = content_equivalence_merge(
                    service,
                    fp_match=fp_match,
                    proposal=safe_proposal,
                    validated_payload=validated_scan.payload,
                    actor=actor,
                    stored_fingerprint=fp,
                    secret_scan_results=(raw_scan, validated_scan),
                    memory_fingerprint_input=memory_fingerprint_input,
                    insert_event=insert_event,
                    raise_memory_status_conflict=raise_memory_status_conflict,
                    parse_payload_json=parse_payload_json,
                    active_confidence_threshold=active_confidence_threshold,
                )
            except InvalidInputError as exc:
                if not is_secret_detected_error(exc):
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

        if row is None or prior_status == "expired":
            status: str = (
                "active" if proposal.confidence >= active_confidence_threshold else "candidate"
            )
            rec = service._insert_memory_and_events(
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
            raise RuntimeError("internal: prior_status required after insert branch")
        service.conn.execute("BEGIN IMMEDIATE")
        try:
            memory_id = int(row["id"])
            prior_payload = parse_payload_json(str(row["payload_json"]))
            prior_payload_clean = coerce_prior_payload_to_schema(
                safe_proposal.memory_type, prior_payload
            )
            merged: dict[str, object] = {**prior_payload_clean, **validated_scan.payload}
            merged_scan = scan_payload_only(merged)
            if merged_scan.verdict is SecretVerdictKind.BLOCK:
                if service.conn.in_transaction:
                    service.conn.rollback()
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
            if new_confidence >= active_confidence_threshold and prior_status == "candidate":
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
                service.conn.commit()
                out.append(service.get_memory(int(row["id"])))
                continue
            merged_fp = content_fingerprint(
                *memory_fingerprint_input(
                    safe_proposal.memory_type,
                    merged,
                    scope=safe_proposal.scope,
                    subject=safe_proposal.subject,
                )
            )
            expected_status = prior_status
            cur = service.conn.execute(
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
                if service.conn.in_transaction:
                    service.conn.rollback()
                raise_memory_status_conflict(memory_id, expected_status)
            insert_event(
                service.conn,
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
                insert_event(service.conn, memory_id, "promoted", {}, actor)
            service.conn.commit()
        except Exception:
            if service.conn.in_transaction:
                service.conn.rollback()
            raise
        out.append(service.get_memory(int(row["id"])))
    return IngestProposalsReport(succeeded=out, failures=failures, suppressed=suppressed)


def content_equivalence_merge(
    service: Any,
    *,
    fp_match: Any,
    proposal: MemoryProposal,
    validated_payload: dict[str, object],
    actor: str,
    stored_fingerprint: str,
    secret_scan_results: tuple[MemorySecretScanResult, ...],
    memory_fingerprint_input: Callable[..., tuple[str, str, str, str, str]],
    insert_event: Callable[..., None],
    raise_memory_status_conflict: Callable[[int, str], None],
    parse_payload_json: Callable[[str], dict[str, object]],
    active_confidence_threshold: float,
) -> MemoryRecord:
    service.conn.execute("BEGIN IMMEDIATE")
    try:
        memory_id = int(fp_match["id"])
        prior_payload = parse_payload_json(str(fp_match["payload_json"]))
        prior_payload_clean = coerce_prior_payload_to_schema(proposal.memory_type, prior_payload)
        merged: dict[str, object] = {**prior_payload_clean, **validated_payload}
        merged_scan = scan_payload_only(merged)
        if merged_scan.verdict is SecretVerdictKind.BLOCK:
            raise_secret_detected(merged_scan)
        merged = merged_scan.payload
        new_confidence = max(float(fp_match["confidence"]), float(proposal.confidence))
        prior_status = str(fp_match["status"])
        new_status = prior_status
        promoted = False
        if new_confidence >= active_confidence_threshold and prior_status == "candidate":
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
            service.conn.commit()
            return cast(MemoryRecord, service.get_memory(memory_id))

        if __debug__:
            merged_fp = content_fingerprint(
                *memory_fingerprint_input(
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
        cur = service.conn.execute(
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
            if service.conn.in_transaction:
                service.conn.rollback()
            raise_memory_status_conflict(memory_id, expected_status)
        insert_event(
            service.conn,
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
            insert_event(service.conn, memory_id, "promoted", {}, actor)
        service.conn.commit()
    except Exception:
        if service.conn.in_transaction:
            service.conn.rollback()
        raise
    return cast(MemoryRecord, service.get_memory(memory_id))
