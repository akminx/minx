from __future__ import annotations

import json
from sqlite3 import Connection


def build_weekly_report(conn: Connection, period_start: str, period_end: str) -> dict[str, object]:
    rows = conn.execute(
        """
        SELECT COALESCE(c.name, 'Uncategorized') AS category_name, ROUND(SUM(t.amount), 2) AS total_amount
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.posted_at BETWEEN ? AND ?
        GROUP BY COALESCE(c.name, 'Uncategorized')
        ORDER BY total_amount ASC
        """,
        (period_start, period_end),
    ).fetchall()
    return {
        "period_start": period_start,
        "period_end": period_end,
        "categories": [dict(row) for row in rows],
    }


def build_monthly_report(conn: Connection, period_start: str, period_end: str) -> dict[str, object]:
    rows = conn.execute(
        """
        SELECT a.name AS account_name, ROUND(SUM(t.amount), 2) AS total_amount
        FROM finance_transactions t
        JOIN finance_accounts a ON a.id = t.account_id
        WHERE t.posted_at BETWEEN ? AND ?
        GROUP BY a.name
        ORDER BY total_amount ASC
        """,
        (period_start, period_end),
    ).fetchall()
    return {
        "period_start": period_start,
        "period_end": period_end,
        "accounts": [dict(row) for row in rows],
    }


def persist_report_run(
    conn: Connection,
    report_kind: str,
    period_start: str,
    period_end: str,
    vault_path: str,
    summary: dict[str, object],
) -> None:
    conn.execute(
        """
        INSERT INTO finance_report_runs (report_kind, period_start, period_end, vault_path, summary_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (report_kind, period_start, period_end, vault_path, json.dumps(summary)),
    )
    conn.commit()
