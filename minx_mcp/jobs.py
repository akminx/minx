from __future__ import annotations

import json
import logging
import uuid
from sqlite3 import Connection, IntegrityError, Row

from minx_mcp.contracts import NotFoundError

STUCK_JOB_TIMEOUT_MINUTES = 30
logger = logging.getLogger(__name__)


def _require_job_row(conn: Connection, job_id: str) -> None:
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Unknown job id: {job_id}")


def submit_job(
    conn: Connection,
    job_type: str,
    requested_by: str | None,
    source_ref: str | None,
    idempotency_key: str | None,
) -> dict[str, object | None]:
    if idempotency_key:
        existing = conn.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            job = _row_to_job(existing)
            if job["status"] == "running":
                stuck = conn.execute(
                    """
                    SELECT 1 FROM jobs
                    WHERE id = ? AND status = 'running'
                      AND updated_at < datetime('now', ?)
                    """,
                    (job["id"], f"-{STUCK_JOB_TIMEOUT_MINUTES} minutes"),
                ).fetchone()
                if stuck:
                    recovery_msg = (
                        f"Auto-recovered: stuck in running for >{STUCK_JOB_TIMEOUT_MINUTES}m"
                    )
                    conn.execute(
                        """
                        UPDATE jobs
                        SET status = 'failed',
                            error_message = ?,
                            idempotency_key = NULL,
                            updated_at = datetime('now')
                        WHERE id = ?
                        """,
                        (recovery_msg, job["id"]),
                    )
                    conn.execute(
                        "INSERT INTO job_events (job_id, status, message) VALUES (?, 'failed', ?)",
                        (job["id"], recovery_msg),
                    )
                    conn.commit()
                    # Intentional fall-through: after recovering the stuck job above,
                    # we continue to insert a new job entry for the current retry attempt.
                else:
                    return job
            else:
                return job

    job_id = str(uuid.uuid4())
    try:
        conn.execute(
            """
            INSERT INTO jobs (id, job_type, status, requested_by, source_ref, idempotency_key)
            VALUES (?, ?, 'queued', ?, ?, ?)
            """,
            (job_id, job_type, requested_by, source_ref, idempotency_key),
        )
    except IntegrityError as exc:
        # String-matching is intentional: sqlite3.IntegrityError does not expose a
        # structured constraint name, so we guard against re-raising on genuinely
        # unexpected integrity errors (e.g. foreign key violations) by checking that
        # the constraint text mentions our known unique index on idempotency_key.
        # This is a race-condition fallback: the upfront SELECT missed a concurrent insert.
        if not idempotency_key or "jobs.idempotency_key" not in str(exc):
            raise
        existing = conn.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if not existing:
            raise
        return _row_to_job(existing)
    conn.execute(
        "INSERT INTO job_events (job_id, status, message) VALUES (?, 'queued', 'Job created')",
        (job_id,),
    )
    conn.commit()
    created_job: dict[str, object | None] | None = get_job(conn, job_id)
    if created_job is None:
        raise RuntimeError(f"Expected queued job {job_id} to exist after insert")
    return created_job


def mark_running(conn: Connection, job_id: str, *, commit: bool = True) -> None:
    _set_status(conn, job_id, "running", None, commit=commit)


def claim_queued_job(conn: Connection, job_id: str) -> bool:
    """Atomically transition queued -> running; return True iff this caller claimed it.

    Prior ``mark_running`` was an unconditional ``UPDATE`` by id, which meant
    two concurrent workers sharing the same idempotency key could both:

      1. Submit the job (idempotent, returns same queued row).
      2. Pass the "status in {running, completed}" early-return gate while
         status is still queued.
      3. Both call ``mark_running`` — the loser would silently overwrite the
         winner's ``completed`` back to ``running``, and both would double-parse
         + double-batch the source file.

    The claim here commits immediately (single-row UPDATE) so other callers
    see ``status='running'`` and bail out of their own parse before doing any
    expensive work. Returns False if the row was not in ``queued`` state at
    claim time, in which case the caller must re-fetch and respect the
    current state.
    """
    _require_job_row(conn, job_id)
    cur = conn.execute(
        """
        UPDATE jobs
        SET status = 'running', updated_at = datetime('now')
        WHERE id = ? AND status = 'queued'
        """,
        (job_id,),
    )
    claimed = cur.rowcount == 1
    if claimed:
        conn.execute(
            "INSERT INTO job_events (job_id, status, message) VALUES (?, 'running', 'Job claimed')",
            (job_id,),
        )
    conn.commit()
    return claimed


def mark_completed(
    conn: Connection, job_id: str, result: dict[str, object], *, commit: bool = True
) -> None:
    _set_status(conn, job_id, "completed", json.dumps(result), commit=commit)


def mark_failed(conn: Connection, job_id: str, message: str, *, commit: bool = True) -> None:
    _require_job_row(conn, job_id)
    conn.execute(
        """
        UPDATE jobs
        SET status = 'failed',
            error_message = ?,
            idempotency_key = NULL,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (message, job_id),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, status, message) VALUES (?, 'failed', ?)",
        (job_id, message),
    )
    if commit:
        conn.commit()


def get_job(conn: Connection, job_id: str) -> dict[str, object | None] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def _set_status(
    conn: Connection, job_id: str, status: str, result_json: str | None, *, commit: bool = True
) -> None:
    _require_job_row(conn, job_id)
    conn.execute(
        """
        UPDATE jobs
        SET status = ?, result_json = COALESCE(?, result_json), updated_at = datetime('now')
        WHERE id = ?
        """,
        (status, result_json, job_id),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, status, message) VALUES (?, ?, ?)",
        (job_id, status, f"Job moved to {status}"),
    )
    if commit:
        conn.commit()


def _row_to_job(row: Row) -> dict[str, object | None]:
    result: object | None
    if row["result_json"]:
        try:
            result = json.loads(row["result_json"])
        except json.JSONDecodeError as exc:
            logger.error(
                "job result_json is corrupt for job_id=%s",
                row["id"],
                extra={"job_id": row["id"], "error_type": type(exc).__name__},
            )
            result = {"error": "corrupt_result_json"}
    else:
        result = None
    return {
        "id": row["id"],
        "job_type": row["job_type"],
        "status": row["status"],
        "requested_by": row["requested_by"],
        "source_ref": row["source_ref"],
        "idempotency_key": row["idempotency_key"],
        "result": result,
        "error_message": row["error_message"],
    }
