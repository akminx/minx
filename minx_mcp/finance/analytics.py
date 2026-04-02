from __future__ import annotations

from sqlite3 import Connection

from minx_mcp.audit import log_sensitive_access


def summarize_finances(conn: Connection) -> dict[str, object]:
    total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM finance_transactions"
    ).fetchone()["total"]
    categories = [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.name AS category_name, ROUND(SUM(t.amount), 2) AS total_amount
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            GROUP BY c.name
            ORDER BY total_amount ASC
            """
        ).fetchall()
    ]
    return {"net_total": total, "categories": categories}


def find_anomalies(conn: Connection) -> dict[str, object]:
    items = []
    for row in conn.execute(
        """
        SELECT t.id, t.description, t.amount, c.name AS category_name
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.amount <= -250 AND COALESCE(c.name, 'Uncategorized') = 'Uncategorized'
        ORDER BY t.amount ASC
        """
    ).fetchall():
        items.append(
            {
                "kind": "large_uncategorized",
                "transaction_id": row["id"],
                "description": row["description"],
                "amount": row["amount"],
            }
        )
    return {"items": items}


def sensitive_query(
    conn: Connection,
    limit: int = 50,
    session_ref: str | None = None,
) -> dict[str, object]:
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT
                t.id,
                t.posted_at,
                t.description,
                t.amount,
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
