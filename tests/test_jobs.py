from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from minx_mcp.contracts import NotFoundError
from minx_mcp.db import get_connection
from minx_mcp.jobs import get_job, mark_completed, mark_failed, mark_running, submit_job


def test_mark_running_unknown_job_raises_not_found(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    fake_id = "00000000-0000-0000-0000-000000000000"
    try:
        mark_running(conn, fake_id)
    except NotFoundError as exc:
        assert fake_id in str(exc)
    else:
        raise AssertionError("expected NotFoundError")


def test_submit_job_reuses_idempotency_key(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    first = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "same-file")
    second = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "same-file")

    assert first["id"] == second["id"]


def test_job_status_transitions_are_persisted(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    job = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "key-1")
    mark_running(conn, job["id"])
    mark_completed(conn, job["id"], {"inserted": 3})

    stored = get_job(conn, job["id"])

    assert stored["status"] == "completed"
    assert stored["result"]["inserted"] == 3


def test_stuck_job_recovery_records_single_failure_event(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    job = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "recover-me")
    mark_running(conn, job["id"])
    conn.execute(
        """
        UPDATE jobs
        SET updated_at = datetime('now', '-31 minutes')
        WHERE id = ?
        """,
        (job["id"],),
    )
    conn.commit()

    recovered = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "recover-me")
    events = conn.execute(
        "SELECT status, message FROM job_events WHERE job_id = ? ORDER BY id",
        (job["id"],),
    ).fetchall()

    assert recovered["id"] != job["id"]
    assert [event["status"] for event in events] == ["queued", "running", "failed"]
    assert events[-1]["message"] == "Auto-recovered: stuck in running for >30m"


def test_failed_job_releases_idempotency_key_for_retry(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    first = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "retry-me")
    mark_failed(conn, first["id"], "boom")

    retried = submit_job(conn, "finance_import", "test", "/tmp/a.csv", "retry-me")

    assert retried["id"] != first["id"]
    assert retried["status"] == "queued"


def test_submit_job_handles_idempotency_race_without_integrity_error(tmp_path):
    barrier = Barrier(2)
    select_gate_count = 0

    class CursorProxy:
        def __init__(self, inner, sql):
            self._inner = inner
            self._sql = sql

        def fetchone(self):
            nonlocal select_gate_count
            if "SELECT * FROM jobs WHERE idempotency_key = ?" in self._sql:
                if select_gate_count < 2:
                    select_gate_count += 1
                    barrier.wait()
            return self._inner.fetchone()

        def __iter__(self):
            return iter(self._inner)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    class ConnectionProxy:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, params=()):
            return CursorProxy(self._inner.execute(sql, params), sql)

        def commit(self):
            return self._inner.commit()

        def close(self):
            return self._inner.close()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def submit_once():
        conn = ConnectionProxy(get_connection(tmp_path / "minx.db"))
        try:
            return submit_job(conn, "finance_import", "test", "/tmp/a.csv", "same-file")
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: submit_once(), range(2)))

    assert first["id"] == second["id"]
