from __future__ import annotations

from datetime import date, timedelta
from sqlite3 import Connection

from minx_mcp.audit import log_sensitive_access
from minx_mcp.money import cents_to_dollars
from minx_mcp.preferences import get_finance_anomaly_threshold_cents


def summarize_finances(conn: Connection) -> dict[str, object]:
    total_cents = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total_cents FROM finance_transactions"
    ).fetchone()["total_cents"]
    categories = [
        {
            "category_name": row["category_name"],
            "total_amount": cents_to_dollars(int(row["total_cents"])),
        }
        for row in conn.execute(
            """
            SELECT c.name AS category_name, COALESCE(SUM(t.amount_cents), 0) AS total_cents
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            GROUP BY c.name
            ORDER BY total_cents ASC
            """
        ).fetchall()
    ]
    return {"net_total": cents_to_dollars(int(total_cents)), "categories": categories}


def find_anomalies(
    conn: Connection,
    period_start: str | None = None,
    end_exclusive: str | None = None,
) -> list[dict[str, object]]:
    threshold = get_finance_anomaly_threshold_cents(conn)
    if period_start and end_exclusive:
        date_clause = "AND t.posted_at >= ? AND t.posted_at < ?"
        params: tuple = (threshold, period_start, end_exclusive)
    else:
        date_clause = ""
        params = (threshold,)

    return [
        {
            "kind": "large_uncategorized",
            "transaction_id": row["id"],
            "posted_at": row["posted_at"],
            "description": row["description"],
            "amount": cents_to_dollars(int(row["amount_cents"])),
        }
        for row in conn.execute(
            f"""
            SELECT t.id, t.posted_at, t.description, t.amount_cents
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.amount_cents <= ?
              AND COALESCE(c.name, 'Uncategorized') = 'Uncategorized'
              {date_clause}
            ORDER BY t.amount_cents ASC, t.id ASC
            """,
            params,
        ).fetchall()
    ]


def find_uncategorized(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> list[dict[str, object]]:
    return [
        {
            "id": row["id"],
            "posted_at": row["posted_at"],
            "description": row["description"],
            "amount": cents_to_dollars(int(row["amount_cents"])),
        }
        for row in conn.execute(
            """
            SELECT
                t.id,
                t.posted_at,
                t.description,
                t.amount_cents
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.posted_at >= ? AND t.posted_at < ?
              AND COALESCE(c.name, 'Uncategorized') = 'Uncategorized'
            ORDER BY t.posted_at ASC, t.id ASC
            """,
            (period_start, end_exclusive),
        ).fetchall()
    ]


def sensitive_query(
    conn: Connection,
    limit: int = 50,
    session_ref: str | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    category_name: str | None = None,
    merchant: str | None = None,
    account_name: str | None = None,
    description_contains: str | None = None,
) -> dict[str, object]:
    clauses, params = _build_sensitive_filter_clauses(
        start_date=start_date,
        end_date=end_date,
        category_name=category_name,
        merchant=merchant,
        account_name=account_name,
        description_contains=description_contains,
    )
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = [
        {
            "id": int(row["id"]),
            "posted_at": str(row["posted_at"]),
            "description": str(row["description"]),
            "account_name": str(row["account_name"]),
            "category_name": row["category_name"],
            "amount": cents_to_dollars(int(row["amount_cents"])),
        }
        for row in conn.execute(
            f"""
            SELECT
                t.id,
                t.posted_at,
                t.description,
                t.amount_cents,
                a.name AS account_name,
                c.name AS category_name
            FROM finance_transactions t
            JOIN finance_accounts a ON a.id = t.account_id
            LEFT JOIN finance_categories c ON c.id = t.category_id
            {where_clause}
            ORDER BY t.posted_at DESC, t.id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    ]
    log_sensitive_access(conn, "sensitive_finance_query", session_ref, f"Returned {len(rows)} rows")
    return {"transactions": rows}


def sensitive_query_total_cents(
    conn: Connection,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    category_name: str | None = None,
    merchant: str | None = None,
    account_name: str | None = None,
    description_contains: str | None = None,
) -> int:
    clauses, params = _build_sensitive_filter_clauses(
        start_date=start_date,
        end_date=end_date,
        category_name=category_name,
        merchant=merchant,
        account_name=account_name,
        description_contains=description_contains,
    )
    clauses.append("t.amount_cents < 0")
    where_clause = f"WHERE {' AND '.join(clauses)}"
    row = conn.execute(
        f"""
        SELECT COALESCE(ABS(SUM(t.amount_cents)), 0) AS total_cents
        FROM finance_transactions t
        JOIN finance_accounts a ON a.id = t.account_id
        LEFT JOIN finance_categories c ON c.id = t.category_id
        {where_clause}
        """,
        params,
    ).fetchone()
    return int(row["total_cents"])


def sensitive_query_count(
    conn: Connection,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    category_name: str | None = None,
    merchant: str | None = None,
    account_name: str | None = None,
    description_contains: str | None = None,
) -> int:
    clauses, params = _build_sensitive_filter_clauses(
        start_date=start_date,
        end_date=end_date,
        category_name=category_name,
        merchant=merchant,
        account_name=account_name,
        description_contains=description_contains,
    )
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total_count
        FROM finance_transactions t
        JOIN finance_accounts a ON a.id = t.account_id
        LEFT JOIN finance_categories c ON c.id = t.category_id
        {where_clause}
        """,
        params,
    ).fetchone()
    return int(row["total_count"])


def _build_sensitive_filter_clauses(
    *,
    start_date: str | None,
    end_date: str | None,
    category_name: str | None,
    merchant: str | None,
    account_name: str | None,
    description_contains: str | None,
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []

    if start_date is not None:
        clauses.append("t.posted_at >= ?")
        params.append(start_date)
    if end_date is not None:
        clauses.append("t.posted_at < ?")
        params.append(_next_day(end_date))
    if category_name is not None:
        clauses.append("COALESCE(c.name, 'Uncategorized') = ?")
        params.append(category_name)
    if merchant is not None:
        clauses.append("t.merchant = ?")
        params.append(merchant)
    if account_name is not None:
        clauses.append("a.name = ?")
        params.append(account_name)
    if description_contains is not None:
        clauses.append("instr(lower(t.description), lower(?)) > 0")
        params.append(description_contains)

    return clauses, params


def _next_day(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()
