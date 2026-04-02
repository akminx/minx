from __future__ import annotations

import json
from datetime import date, timedelta
from sqlite3 import Connection


def build_weekly_report(conn: Connection, period_start: str, period_end: str) -> dict[str, object]:
    prior_start, prior_end = _previous_window(period_start, period_end)
    totals = dict(
        conn.execute(
            """
            SELECT
                ROUND(COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0), 2) AS inflow,
                ROUND(COALESCE(ABS(SUM(CASE WHEN amount < 0 THEN amount END)), 0), 2) AS outflow
            FROM finance_transactions
            WHERE posted_at BETWEEN ? AND ?
            """,
            (period_start, period_end),
        ).fetchone()
    )
    top_categories = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                COALESCE(c.name, 'Uncategorized') AS category_name,
                ROUND(ABS(SUM(t.amount)), 2) AS total_outflow
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.posted_at BETWEEN ? AND ? AND t.amount < 0
            GROUP BY COALESCE(c.name, 'Uncategorized')
            ORDER BY total_outflow DESC, category_name ASC
            LIMIT 5
            """,
            (period_start, period_end),
        ).fetchall()
    ]
    notable_merchants = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                merchant,
                ROUND(ABS(SUM(amount)), 2) AS total_outflow,
                COUNT(*) AS transaction_count
            FROM finance_transactions
            WHERE posted_at BETWEEN ? AND ?
              AND amount < 0
              AND COALESCE(merchant, '') != ''
            GROUP BY merchant
            ORDER BY total_outflow DESC, merchant ASC
            LIMIT 5
            """,
            (period_start, period_end),
        ).fetchall()
    ]
    current_categories = _category_outflow_map(conn, period_start, period_end)
    prior_categories = _category_outflow_map(conn, prior_start, prior_end)
    category_changes = [
        {
            "category_name": category_name,
            "current_outflow": current_categories.get(category_name, 0.0),
            "prior_outflow": prior_categories.get(category_name, 0.0),
            "delta_outflow": round(
                current_categories.get(category_name, 0.0)
                - prior_categories.get(category_name, 0.0),
                2,
            ),
        }
        for category_name in sorted(set(current_categories) | set(prior_categories))
    ]
    category_changes.sort(
        key=lambda item: (-abs(float(item["delta_outflow"])), str(item["category_name"]))
    )

    return {
        "period_start": period_start,
        "period_end": period_end,
        "totals": totals,
        "top_categories": top_categories,
        "notable_merchants": notable_merchants,
        "category_changes": category_changes,
        "anomalies": _period_anomalies(conn, period_start, period_end),
        "uncategorized_transactions": _uncategorized_transactions(conn, period_start, period_end),
    }


def build_monthly_report(conn: Connection, period_start: str, period_end: str) -> dict[str, object]:
    prior_start, prior_end = _previous_month_window(period_start)
    account_rollups = [
        dict(row)
        for row in conn.execute(
            """
            SELECT a.name AS account_name, ROUND(SUM(t.amount), 2) AS total_amount
            FROM finance_transactions t
            JOIN finance_accounts a ON a.id = t.account_id
            WHERE t.posted_at BETWEEN ? AND ?
            GROUP BY a.name
            ORDER BY total_amount ASC, account_name ASC
            """,
            (period_start, period_end),
        ).fetchall()
    ]
    category_totals = [
        dict(row)
        for row in conn.execute(
            """
            SELECT COALESCE(c.name, 'Uncategorized') AS category_name, ROUND(SUM(t.amount), 2) AS total_amount
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.posted_at BETWEEN ? AND ?
            GROUP BY COALESCE(c.name, 'Uncategorized')
            ORDER BY total_amount ASC, category_name ASC
            """,
            (period_start, period_end),
        ).fetchall()
    ]
    current_accounts = _account_total_map(conn, period_start, period_end)
    prior_accounts = _account_total_map(conn, prior_start, prior_end)
    changes_vs_prior_month = [
        {
            "account_name": account_name,
            "current_total": current_accounts.get(account_name, 0.0),
            "prior_total": prior_accounts.get(account_name, 0.0),
            "delta_total": round(
                current_accounts.get(account_name, 0.0)
                - prior_accounts.get(account_name, 0.0),
                2,
            ),
        }
        for account_name in sorted(set(current_accounts) | set(prior_accounts))
    ]
    recurring_charge_highlights = _recurring_charge_highlights(
        conn,
        period_start,
        period_end,
        prior_start,
        prior_end,
    )

    return {
        "period_start": period_start,
        "period_end": period_end,
        "account_rollups": account_rollups,
        "category_totals": category_totals,
        "changes_vs_prior_month": changes_vs_prior_month,
        "recurring_charge_highlights": recurring_charge_highlights,
        "anomalies": _period_anomalies(conn, period_start, period_end),
        "uncategorized_or_new_merchants": _monthly_review_items(conn, period_start, period_end),
        "accounts": account_rollups,
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


def _previous_window(period_start: str, period_end: str) -> tuple[str, str]:
    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    duration = end - start
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - duration
    return prior_start.isoformat(), prior_end.isoformat()


def _previous_month_window(period_start: str) -> tuple[str, str]:
    start = date.fromisoformat(period_start)
    prior_end = start - timedelta(days=1)
    prior_start = prior_end.replace(day=1)
    return prior_start.isoformat(), prior_end.isoformat()


def _category_outflow_map(conn: Connection, period_start: str, period_end: str) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT
            COALESCE(c.name, 'Uncategorized') AS category_name,
            ROUND(ABS(SUM(t.amount)), 2) AS total_outflow
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.posted_at BETWEEN ? AND ? AND t.amount < 0
        GROUP BY COALESCE(c.name, 'Uncategorized')
        """,
        (period_start, period_end),
    ).fetchall()
    return {str(row["category_name"]): float(row["total_outflow"]) for row in rows}


def _account_total_map(conn: Connection, period_start: str, period_end: str) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT a.name AS account_name, ROUND(SUM(t.amount), 2) AS total_amount
        FROM finance_transactions t
        JOIN finance_accounts a ON a.id = t.account_id
        WHERE t.posted_at BETWEEN ? AND ?
        GROUP BY a.name
        """,
        (period_start, period_end),
    ).fetchall()
    return {str(row["account_name"]): float(row["total_amount"]) for row in rows}


def _period_anomalies(conn: Connection, period_start: str, period_end: str) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                'large_uncategorized' AS kind,
                t.id AS transaction_id,
                t.posted_at,
                t.description,
                t.amount
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.posted_at BETWEEN ? AND ?
              AND t.amount <= -250
              AND COALESCE(c.name, 'Uncategorized') = 'Uncategorized'
            ORDER BY t.amount ASC, t.id ASC
            """,
            (period_start, period_end),
        ).fetchall()
    ]


def _uncategorized_transactions(
    conn: Connection,
    period_start: str,
    period_end: str,
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                t.id,
                t.posted_at,
                t.description,
                t.amount
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.posted_at BETWEEN ? AND ?
              AND COALESCE(c.name, 'Uncategorized') = 'Uncategorized'
            ORDER BY t.posted_at ASC, t.id ASC
            """,
            (period_start, period_end),
        ).fetchall()
    ]


def _recurring_charge_highlights(
    conn: Connection,
    period_start: str,
    period_end: str,
    prior_start: str,
    prior_end: str,
) -> list[dict[str, object]]:
    current_rows = conn.execute(
        """
        SELECT merchant, COUNT(*) AS transaction_count, ROUND(ABS(SUM(amount)), 2) AS total_outflow
        FROM finance_transactions
        WHERE posted_at BETWEEN ? AND ?
          AND amount < 0
          AND COALESCE(merchant, '') != ''
        GROUP BY merchant
        """,
        (period_start, period_end),
    ).fetchall()
    prior_rows = conn.execute(
        """
        SELECT merchant, COUNT(*) AS transaction_count, ROUND(ABS(SUM(amount)), 2) AS total_outflow
        FROM finance_transactions
        WHERE posted_at BETWEEN ? AND ?
          AND amount < 0
          AND COALESCE(merchant, '') != ''
        GROUP BY merchant
        """,
        (prior_start, prior_end),
    ).fetchall()
    current = {str(row["merchant"]): dict(row) for row in current_rows}
    prior = {str(row["merchant"]): dict(row) for row in prior_rows}

    highlights = []
    for merchant in sorted(set(current) & set(prior)):
        highlights.append(
            {
                "merchant": merchant,
                "current_outflow": float(current[merchant]["total_outflow"]),
                "prior_outflow": float(prior[merchant]["total_outflow"]),
                "current_count": int(current[merchant]["transaction_count"]),
                "prior_count": int(prior[merchant]["transaction_count"]),
            }
        )
    highlights.sort(key=lambda item: (-float(item["current_outflow"]), str(item["merchant"])))
    return highlights


def _monthly_review_items(
    conn: Connection,
    period_start: str,
    period_end: str,
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for row in _uncategorized_transactions(conn, period_start, period_end):
        items.append(
            {
                "kind": "uncategorized_transaction",
                "posted_at": row["posted_at"],
                "description": row["description"],
                "amount": row["amount"],
            }
        )

    new_merchants = conn.execute(
        """
        SELECT
            t.merchant,
            MIN(t.posted_at) AS first_seen_at,
            ROUND(SUM(t.amount), 2) AS total_amount
        FROM finance_transactions t
        WHERE t.posted_at BETWEEN ? AND ?
          AND COALESCE(t.merchant, '') != ''
          AND NOT EXISTS (
              SELECT 1
              FROM finance_transactions earlier
              WHERE earlier.merchant = t.merchant
                AND earlier.posted_at < ?
          )
        GROUP BY t.merchant
        ORDER BY total_amount ASC, t.merchant ASC
        """,
        (period_start, period_end, period_start),
    ).fetchall()
    for row in new_merchants:
        items.append(
            {
                "kind": "new_merchant",
                "merchant": row["merchant"],
                "first_seen_at": row["first_seen_at"],
                "total_amount": row["total_amount"],
            }
        )
    return items
