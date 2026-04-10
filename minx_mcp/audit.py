from __future__ import annotations

from sqlite3 import Connection


def log_sensitive_access(
    conn: Connection,
    tool_name: str,
    session_ref: str | None,
    summary: str,
) -> None:
    should_commit = not conn.in_transaction
    conn.execute(
        "INSERT INTO audit_log (tool_name, session_ref, summary) VALUES (?, ?, ?)",
        (tool_name, session_ref, summary),
    )
    if should_commit:
        conn.commit()
