"""Write finance markdown reports: validate windows, vault file, DB lifecycle, events."""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from sqlite3 import Connection
from typing import Literal, Protocol

from minx_mcp.contracts import InvalidInputError
from minx_mcp.finance.report_builders import (
    build_monthly_report,
    build_weekly_report,
    render_monthly_markdown,
    render_weekly_markdown,
)
from minx_mcp.vault_writer import VaultWriter

logger = logging.getLogger(__name__)

ReportKind = Literal["weekly", "monthly"]
REPORT_RUN_STATUSES = frozenset({"pending", "completed", "failed"})


class FinanceReportHost(Protocol):
    """Internal surface used by report orchestration (implemented by `FinanceService`).

    The protocol intentionally mirrors the existing private event helper so the
    refactor does not widen `FinanceService`'s outward API surface.
    """

    @property
    def conn(self) -> Connection: ...

    vault_writer: VaultWriter

    def _emit_finance_event(
        self,
        *,
        event_type: str,
        entity_ref: str | None,
        payload: dict[str, object],
    ) -> int: ...


def run_weekly_report(
    host: FinanceReportHost, period_start: str, period_end: str
) -> dict[str, object]:
    validate_weekly_window(period_start, period_end)
    summary = build_weekly_report(host.conn, period_start, period_end)
    summary_payload = summary.to_dict()
    content = render_weekly_markdown(summary, period_start, period_end)
    relative_path = f"Finance/weekly-{period_start}.md"
    return _write_report_artifact(
        host,
        "weekly",
        period_start,
        period_end,
        summary_payload,
        content,
        relative_path,
    )


def run_monthly_report(
    host: FinanceReportHost, period_start: str, period_end: str
) -> dict[str, object]:
    validate_monthly_window(period_start, period_end)
    summary = build_monthly_report(host.conn, period_start, period_end)
    summary_payload = summary.to_dict()
    content = render_monthly_markdown(summary, period_start, period_end)
    relative_path = f"Finance/monthly-{period_start[:7]}.md"
    return _write_report_artifact(
        host,
        "monthly",
        period_start,
        period_end,
        summary_payload,
        content,
        relative_path,
    )


def _write_report_artifact(
    host: FinanceReportHost,
    report_type: ReportKind,
    period_start: str,
    period_end: str,
    summary_payload: dict[str, object],
    markdown: str,
    relative_path: str,
) -> dict[str, object]:
    planned_path = host.vault_writer.resolve_path(relative_path)
    upsert_report_run(
        host.conn,
        report_type,
        period_start,
        period_end,
        str(planned_path),
        summary_payload,
        status="pending",
    )
    path: Path | None = None
    try:
        path = host.vault_writer.write_markdown(relative_path, markdown)
        host._emit_finance_event(
            event_type="finance.report_generated",
            entity_ref=str(path),
            payload={
                "report_type": report_type,
                "period_start": period_start,
                "period_end": period_end,
                "vault_path": str(path),
            },
        )
        persist_report_run(
            host.conn,
            report_type,
            period_start,
            period_end,
            str(path),
            summary_payload,
        )
    except Exception as exc:
        # Pending rows are committed before the vault write so retries can repair
        # the report window deterministically. Roll back only if a later statement
        # opened a transaction before we mark the run as failed.
        if host.conn.in_transaction:
            host.conn.rollback()
        failed_path = path or planned_path
        best_effort_unlink(failed_path)
        upsert_report_run(
            host.conn,
            report_type,
            period_start,
            period_end,
            str(failed_path),
            summary_payload,
            status="failed",
            error_message=str(exc),
        )
        raise
    return {"vault_path": str(path), "summary": summary_payload}


def validate_weekly_window(period_start: str, period_end: str) -> None:
    start, end = _parse_date_window(period_start, period_end)
    if (end - start).days != 6:
        raise InvalidInputError("weekly reports must span exactly 7 days")


def validate_monthly_window(period_start: str, period_end: str) -> None:
    start, end = _parse_date_window(period_start, period_end)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    expected_end = next_month - timedelta(days=1)
    if start.day != 1 or end != expected_end:
        raise InvalidInputError("monthly reports must cover a full calendar month")


def _parse_date_window(period_start: str, period_end: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
    except ValueError as exc:
        raise InvalidInputError("Invalid ISO date") from exc
    if start > end:
        raise InvalidInputError("period_start must be on or before period_end")
    return start, end


def best_effort_unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        logger.warning("Unable to remove failed report artifact %s: %s", path, exc)


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
