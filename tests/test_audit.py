from minx_mcp.audit import log_sensitive_access
from minx_mcp.db import get_connection


def test_sensitive_access_is_logged(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    log_sensitive_access(conn, "sensitive_finance_query", "session-1", "Queried March transactions")

    row = conn.execute("SELECT tool_name, session_ref, summary FROM audit_log").fetchone()

    assert dict(row) == {
        "tool_name": "sensitive_finance_query",
        "session_ref": "session-1",
        "summary": "Queried March transactions",
    }
