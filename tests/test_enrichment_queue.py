from __future__ import annotations

import json

from minx_mcp.core.enrichment_queue import (
    enqueue_enrichment_job,
    enrichment_status,
    retry_dead_letter,
    sweep_enrichment_queue,
)
from minx_mcp.db import get_connection


def test_enqueue_enrichment_job_and_status_counts(tmp_path) -> None:
    conn = get_connection(tmp_path / "m.db")

    job = enqueue_enrichment_job(
        conn,
        job_type="memory.embedding",
        subject_type="memory",
        subject_id=42,
        payload={"memory_id": 42},
        priority=10,
    )

    assert job.id > 0
    assert job.status == "queued"
    assert json.loads(job.payload_json) == {"memory_id": 42}
    assert enrichment_status(conn)["queued"] == 1


def test_sweep_requeues_then_dead_letters_missing_handler(tmp_path) -> None:
    conn = get_connection(tmp_path / "m.db")
    job = enqueue_enrichment_job(
        conn,
        job_type="memory.embedding",
        subject_type="memory",
        subject_id=42,
        payload={"memory_id": 42},
        max_attempts=2,
    )

    first = sweep_enrichment_queue(conn, limit=10)
    conn.execute("UPDATE enrichment_jobs SET available_at = datetime('now', '-1 second') WHERE id = ?", (job.id,))
    conn.commit()
    second = sweep_enrichment_queue(conn, limit=10)

    assert first.claimed == 1
    assert first.failed == 1
    assert first.dead_lettered == 0
    assert second.claimed == 1
    assert second.failed == 0
    assert second.dead_lettered == 1
    row = conn.execute("SELECT status, attempts, last_error FROM enrichment_jobs WHERE id = ?", (job.id,)).fetchone()
    assert dict(row) == {
        "status": "dead_letter",
        "attempts": 2,
        "last_error": "no enrichment handler registered for job_type=memory.embedding",
    }


def test_sweep_delays_retry_after_non_terminal_failure(tmp_path) -> None:
    conn = get_connection(tmp_path / "m.db")
    job = enqueue_enrichment_job(
        conn,
        job_type="memory.embedding",
        subject_type="memory",
        subject_id=42,
        payload={"memory_id": 42},
        max_attempts=3,
    )
    original_available_at = job.available_at

    first = sweep_enrichment_queue(conn, limit=10)
    second = sweep_enrichment_queue(conn, limit=10)

    assert first.claimed == 1
    assert first.failed == 1
    assert second.claimed == 0
    row = conn.execute("SELECT status, attempts, available_at FROM enrichment_jobs WHERE id = ?", (job.id,)).fetchone()
    assert row["status"] == "queued"
    assert row["attempts"] == 1
    assert row["available_at"] > original_available_at
    assert row["available_at"] > conn.execute("SELECT datetime('now')").fetchone()[0]


def test_retry_dead_letter_resets_job_to_queued(tmp_path) -> None:
    conn = get_connection(tmp_path / "m.db")
    job = enqueue_enrichment_job(
        conn,
        job_type="memory.embedding",
        subject_type="memory",
        subject_id=42,
        payload={"memory_id": 42},
        max_attempts=1,
    )
    sweep_enrichment_queue(conn, limit=10)

    retried = retry_dead_letter(conn, job.id)

    assert retried.status == "queued"
    assert retried.attempts == 0
    assert retried.last_error is None


def test_sweep_recovers_stale_running_jobs_before_claim(tmp_path) -> None:
    conn = get_connection(tmp_path / "m.db")
    job = enqueue_enrichment_job(
        conn,
        job_type="memory.embedding",
        subject_type="memory",
        subject_id=42,
        payload={"memory_id": 42},
        max_attempts=2,
    )
    conn.execute(
        """
        UPDATE enrichment_jobs
        SET status = 'running',
            locked_at = datetime('now', '-1 hour')
        WHERE id = ?
        """,
        (job.id,),
    )
    conn.commit()

    report = sweep_enrichment_queue(conn, limit=10, handlers={"memory.embedding": lambda _job: {"ok": True}})

    assert report.claimed == 1
    assert report.succeeded == 1
    row = conn.execute("SELECT status, attempts, last_error FROM enrichment_jobs WHERE id = ?", (job.id,)).fetchone()
    assert dict(row) == {"status": "succeeded", "attempts": 1, "last_error": None}
