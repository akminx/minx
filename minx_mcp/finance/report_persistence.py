"""Persist finance report run metadata to SQLite (idempotent upserts, status validation)."""

from __future__ import annotations

import json
from sqlite3 import Connection

from minx_mcp.contracts import InvalidInputError

REPORT_RUN_STATUSES = frozenset({"pending", "completed", "failed"})


def persist_report_run(
    conn: Connection,
    report_kind: str,
    period_start: str,
    period_end: str,
    vault_path: str,
    summary: dict[str, object],
) -> None:
    _upsert_report_run(
        conn,
        report_kind,
        period_start,
        period_end,
        vault_path,
        summary,
        status="completed",
        error_message=None,
        commit=True,
    )


def upsert_report_run(
    conn: Connection,
    report_kind: str,
    period_start: str,
    period_end: str,
    vault_path: str,
    summary: dict[str, object],
    *,
    status: str,
    error_message: str | None = None,
    commit: bool = True,
) -> None:
    _upsert_report_run(
        conn,
        report_kind,
        period_start,
        period_end,
        vault_path,
        summary,
        status=status,
        error_message=error_message,
        commit=commit,
    )


def _upsert_report_run(
    conn: Connection,
    report_kind: str,
    period_start: str,
    period_end: str,
    vault_path: str,
    summary: dict[str, object],
    *,
    status: str,
    error_message: str | None,
    commit: bool,
) -> None:
    if status not in REPORT_RUN_STATUSES:
        allowed = ", ".join(sorted(REPORT_RUN_STATUSES))
        raise InvalidInputError(f"report run status must be one of: {allowed}")
    conn.execute(
        """
        INSERT INTO finance_report_runs (
            report_kind,
            period_start,
            period_end,
            vault_path,
            summary_json,
            status,
            error_message
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_kind, period_start, period_end)
        DO UPDATE SET
            vault_path = excluded.vault_path,
            summary_json = excluded.summary_json,
            status = excluded.status,
            error_message = excluded.error_message,
            updated_at = datetime('now')
        """,
        (
            report_kind,
            period_start,
            period_end,
            vault_path,
            json.dumps(summary),
            status,
            error_message,
        ),
    )
    if commit:
        conn.commit()
