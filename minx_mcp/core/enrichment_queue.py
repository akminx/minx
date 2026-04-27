"""Durable enrichment queue primitives for background memory work."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any

from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.secret_scanner import scan_for_secrets
from minx_mcp.validation import require_non_empty

_STATUSES = ("queued", "running", "succeeded", "failed", "dead_letter")
_RUNNING_LEASE_TIMEOUT_MINUTES = 30
EnrichmentHandler = Callable[["EnrichmentJob"], dict[str, object] | None]


@dataclass(frozen=True)
class EnrichmentJob:
    id: int
    job_type: str
    subject_type: str
    subject_id: int
    payload_json: str
    status: str
    priority: int
    attempts: int
    max_attempts: int
    available_at: str
    locked_at: str | None
    completed_at: str | None
    last_error: str | None
    result_json: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class EnrichmentSweepReport:
    claimed: int
    succeeded: int
    failed: int
    dead_lettered: int


def enqueue_enrichment_job(
    conn: Connection,
    *,
    job_type: str,
    subject_type: str,
    subject_id: int,
    payload: dict[str, object] | None = None,
    priority: int = 100,
    max_attempts: int = 3,
) -> EnrichmentJob:
    jt = require_non_empty("job_type", job_type)
    st = require_non_empty("subject_type", subject_type)
    sid = _validate_positive_int("subject_id", subject_id)
    prio = _validate_int("priority", priority)
    max_try = _validate_positive_int("max_attempts", max_attempts)
    payload_json = json.dumps(payload or {}, sort_keys=True)
    _block_secret_payload(payload_json)
    cur = conn.execute(
        """
        INSERT INTO enrichment_jobs (
            job_type, subject_type, subject_id, payload_json, priority, max_attempts
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (jt, st, sid, payload_json, prio, max_try),
    )
    conn.commit()
    if cur.lastrowid is None:
        raise RuntimeError("enrichment job insert did not return a row id")
    return _get_job(conn, int(cur.lastrowid))


def enrichment_status(conn: Connection) -> dict[str, int]:
    counts = dict.fromkeys(_STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS c FROM enrichment_jobs GROUP BY status"):
        counts[str(row["status"])] = int(row["c"])
    return counts


def sweep_enrichment_queue(
    conn: Connection,
    *,
    limit: int,
    handlers: Mapping[str, EnrichmentHandler] | None = None,
) -> EnrichmentSweepReport:
    lim = _validate_sweep_limit(limit)
    claimed = _claim_jobs(conn, lim)
    handler_map = handlers or {}
    succeeded = 0
    failed = 0
    dead_lettered = 0
    for job in claimed:
        handler = handler_map.get(job.job_type)
        if handler is None:
            became_dead = _fail_job(
                conn,
                job,
                f"no enrichment handler registered for job_type={job.job_type}",
            )
            if became_dead:
                dead_lettered += 1
            else:
                failed += 1
            continue
        try:
            result = handler(job) or {}
        except Exception as exc:  # pragma: no cover - handler failures are tested by callers.
            became_dead = _fail_job(conn, job, str(exc))
            if became_dead:
                dead_lettered += 1
            else:
                failed += 1
            continue
        _complete_job(conn, job.id, result)
        succeeded += 1
    return EnrichmentSweepReport(
        claimed=len(claimed),
        succeeded=succeeded,
        failed=failed,
        dead_lettered=dead_lettered,
    )


def retry_dead_letter(conn: Connection, job_id: int) -> EnrichmentJob:
    jid = _validate_positive_int("job_id", job_id)
    row = conn.execute("SELECT * FROM enrichment_jobs WHERE id = ?", (jid,)).fetchone()
    if row is None:
        raise NotFoundError(f"enrichment job {jid} not found")
    if str(row["status"]) != "dead_letter":
        raise InvalidInputError("enrichment job is not in dead_letter status")
    conn.execute(
        """
        UPDATE enrichment_jobs
        SET status = 'queued',
            attempts = 0,
            locked_at = NULL,
            completed_at = NULL,
            last_error = NULL,
            result_json = NULL,
            available_at = datetime('now'),
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (jid,),
    )
    conn.commit()
    return _get_job(conn, jid)


def _claim_jobs(conn: Connection, limit: int) -> list[EnrichmentJob]:
    _recover_stale_running_jobs(conn)
    rows = conn.execute(
        """
        SELECT *
        FROM enrichment_jobs
        WHERE status = 'queued'
          AND available_at <= datetime('now')
        ORDER BY priority ASC, id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    jobs: list[EnrichmentJob] = []
    for row in rows:
        job_id = int(row["id"])
        cur = conn.execute(
            """
            UPDATE enrichment_jobs
            SET status = 'running',
                attempts = attempts + 1,
                locked_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ? AND status = 'queued'
            """,
            (job_id,),
        )
        if cur.rowcount == 1:
            jobs.append(_get_job(conn, job_id))
    conn.commit()
    return jobs


def _recover_stale_running_jobs(conn: Connection) -> None:
    conn.execute(
        """
        UPDATE enrichment_jobs
        SET status = 'queued',
            locked_at = NULL,
            last_error = 'recovered stale running job',
            available_at = datetime('now'),
            updated_at = datetime('now')
        WHERE status = 'running'
          AND locked_at IS NOT NULL
          AND locked_at <= datetime('now', ?)
        """,
        (f"-{_RUNNING_LEASE_TIMEOUT_MINUTES} minutes",),
    )


def _complete_job(conn: Connection, job_id: int, result: dict[str, object]) -> None:
    conn.execute(
        """
        UPDATE enrichment_jobs
        SET status = 'succeeded',
            completed_at = datetime('now'),
            result_json = ?,
            last_error = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (json.dumps(result, sort_keys=True), job_id),
    )
    conn.commit()


def _fail_job(conn: Connection, job: EnrichmentJob, error: str) -> bool:
    status = "dead_letter" if job.attempts >= job.max_attempts else "queued"
    conn.execute(
        """
        UPDATE enrichment_jobs
        SET status = ?,
            locked_at = NULL,
            last_error = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (status, error, job.id),
    )
    conn.commit()
    return status == "dead_letter"


def _get_job(conn: Connection, job_id: int) -> EnrichmentJob:
    row = conn.execute("SELECT * FROM enrichment_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"enrichment job {job_id} not found")
    return _row_to_job(row)


def _row_to_job(row: Any) -> EnrichmentJob:
    return EnrichmentJob(
        id=int(row["id"]),
        job_type=str(row["job_type"]),
        subject_type=str(row["subject_type"]),
        subject_id=int(row["subject_id"]),
        payload_json=str(row["payload_json"]),
        status=str(row["status"]),
        priority=int(row["priority"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        available_at=str(row["available_at"]),
        locked_at=row["locked_at"] if row["locked_at"] is not None else None,
        completed_at=row["completed_at"] if row["completed_at"] is not None else None,
        last_error=row["last_error"] if row["last_error"] is not None else None,
        result_json=row["result_json"] if row["result_json"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _block_secret_payload(payload_json: str) -> None:
    verdict = scan_for_secrets(payload_json)
    if verdict.findings:
        raise InvalidInputError(
            "Secret detected in enrichment payload",
            data={
                "kind": "secret_detected",
                "surface": "enrichment_queue",
                "detected_kinds": sorted({finding.kind for finding in verdict.findings}),
            },
        )


def _validate_int(field: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError(f"{field} must be an integer")
    return value


def _validate_positive_int(field: str, value: int) -> int:
    out = _validate_int(field, value)
    if out < 1:
        raise InvalidInputError(f"{field} must be positive")
    return out


def _validate_sweep_limit(limit: int) -> int:
    out = _validate_positive_int("limit", limit)
    if out > 100:
        raise InvalidInputError("limit must be between 1 and 100")
    return out
