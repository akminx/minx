from minx_mcp.db import get_connection
from minx_mcp.jobs import get_job, mark_completed, mark_running, submit_job


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
