from __future__ import annotations

from sqlite3 import Connection

from minx_mcp.audit import log_sensitive_access
from minx_mcp.money import cents_to_dollars

ANOMALY_THRESHOLD = -25_000


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
    if period_start and end_exclusive:
        date_clause = "AND t.posted_at >= ? AND t.posted_at < ?"
        params: tuple = (ANOMALY_THRESHOLD, period_start, end_exclusive)
    else:
        date_clause = ""
        params = (ANOMALY_THRESHOLD,)

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
) -> dict[str, object]:
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
            """
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
            ORDER BY t.posted_at DESC, t.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]
    log_sensitive_access(conn, "sensitive_finance_query", session_ref, f"Returned {len(rows)} rows")
    return {"transactions": rows}
