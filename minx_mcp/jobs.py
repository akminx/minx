from __future__ import annotations

import json
import uuid
from sqlite3 import Connection, Row


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
            return _row_to_job(existing)

    job_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO jobs (id, job_type, status, requested_by, source_ref, idempotency_key)
        VALUES (?, ?, 'queued', ?, ?, ?)
        """,
        (job_id, job_type, requested_by, source_ref, idempotency_key),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, status, message) VALUES (?, 'queued', 'Job created')",
        (job_id,),
    )
    conn.commit()
    return get_job(conn, job_id)


def mark_running(conn: Connection, job_id: str) -> None:
    _set_status(conn, job_id, "running", None)


def mark_completed(conn: Connection, job_id: str, result: dict[str, object]) -> None:
    _set_status(conn, job_id, "completed", json.dumps(result))


def mark_failed(conn: Connection, job_id: str, message: str) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status = 'failed', error_message = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (message, job_id),
    )
    conn.execute(
        "INSERT INTO job_events (job_id, status, message) VALUES (?, 'failed', ?)",
        (job_id, message),
    )
    conn.commit()


def get_job(conn: Connection, job_id: str) -> dict[str, object | None] | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def _set_status(conn: Connection, job_id: str, status: str, result_json: str | None) -> None:
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
    conn.commit()


def _row_to_job(row: Row) -> dict[str, object | None]:
    result = json.loads(row["result_json"]) if row["result_json"] else None
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
