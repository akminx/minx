from __future__ import annotations

from datetime import date, timedelta
from sqlite3 import Connection

from minx_mcp.audit import log_sensitive_access
from minx_mcp.money import cents_to_display_dollars
from minx_mcp.preferences import get_finance_anomaly_threshold_cents
from minx_mcp.time_utils import next_day


def summarize_finances(conn: Connection) -> dict[str, object]:
    total_cents = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) AS total_cents FROM finance_transactions"
    ).fetchone()["total_cents"]
    categories = [
        {
            "category_name": row["category_name"],
            "total_amount": cents_to_display_dollars(int(row["total_cents"])),
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
    return {"net_total": cents_to_display_dollars(int(total_cents)), "categories": categories}


def find_anomalies(
    conn: Connection,
    period_start: str | None = None,
    end_exclusive: str | None = None,
) -> list[dict[str, object]]:
    threshold = get_finance_anomaly_threshold_cents(conn)
    if period_start and end_exclusive:
        date_clause = "AND t.posted_at >= ? AND t.posted_at < ?"
        params: tuple[int | str, ...] = (threshold, period_start, end_exclusive)
    else:
        date_clause = ""
        params = (threshold,)

    # Safe: date_clause is either empty or fixed AND ... ? ... ?; dates bind via params.
    return [
        {
            "kind": "large_uncategorized",
            "transaction_id": row["id"],
            "posted_at": row["posted_at"],
            "description": row["description"],
            "amount": cents_to_display_dollars(int(row["amount_cents"])),
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
            """,  # noqa: S608
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
            "amount": cents_to_display_dollars(int(row["amount_cents"])),
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
    audit_tool_name: str = "sensitive_finance_query",
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
    # Safe: WHERE is AND of _build_sensitive_filter_clauses templates (? only); values bound in params/limit.
    rows = [
        {
            "id": int(row["id"]),
            "posted_at": str(row["posted_at"]),
            "description": str(row["description"]),
            "merchant": row["merchant"],
            "raw_merchant": row["raw_merchant"],
            "account_name": str(row["account_name"]),
            "category_name": row["category_name"],
            "amount": cents_to_display_dollars(int(row["amount_cents"])),
        }
        for row in conn.execute(
            f"""
            SELECT
                t.id,
                t.posted_at,
                t.description,
                t.merchant,
                t.raw_merchant,
                t.amount_cents,
                a.name AS account_name,
                c.name AS category_name
            FROM finance_transactions t
            JOIN finance_accounts a ON a.id = t.account_id
            LEFT JOIN finance_categories c ON c.id = t.category_id
            {where_clause}
            ORDER BY t.posted_at DESC, t.id DESC
            LIMIT ?
            """,  # noqa: S608
            [*params, limit],
        ).fetchall()
    ]
    log_sensitive_access(conn, audit_tool_name, session_ref, f"Returned {len(rows)} rows")
    return {"transactions": rows}


def build_finance_monitoring(
    conn: Connection,
    *,
    period_start: str,
    period_end: str,
) -> dict[str, object]:
    prior_start, prior_end = _prior_period_window(period_start, period_end)
    current_rows = conn.execute(
        """
        SELECT
            COALESCE(c.name, 'Uncategorized') AS category_name,
            COALESCE(ABS(SUM(t.amount_cents)), 0) AS total_spent_cents
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND t.amount_cents < 0
        GROUP BY COALESCE(c.name, 'Uncategorized')
        ORDER BY total_spent_cents DESC, category_name ASC
        """,
        (period_start, next_day(period_end)),
    ).fetchall()
    current_totals = {
        str(row["category_name"]): int(row["total_spent_cents"]) for row in current_rows
    }
    merchant_rows = conn.execute(
        """
        SELECT
            COALESCE(t.merchant, 'Unknown') AS merchant,
            COALESCE(ABS(SUM(t.amount_cents)), 0) AS total_spent_cents,
            COUNT(*) AS transaction_count
        FROM finance_transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND t.amount_cents < 0
        GROUP BY COALESCE(t.merchant, 'Unknown')
        ORDER BY total_spent_cents DESC, merchant ASC
        """,
        (period_start, next_day(period_end)),
    ).fetchall()
    income_rows = conn.execute(
        """
        SELECT
            COALESCE(t.merchant, t.description, 'Unknown') AS merchant,
            COUNT(*) AS transaction_count,
            COALESCE(SUM(t.amount_cents), 0) AS total_income_cents
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND t.amount_cents > 0
          AND COALESCE(c.name, '') = 'Income'
        GROUP BY COALESCE(t.merchant, t.description, 'Unknown')
        ORDER BY total_income_cents DESC, merchant ASC
        """,
        (period_start, next_day(period_end)),
    ).fetchall()
    uncategorized_row = conn.execute(
        """
        SELECT
            COUNT(*) AS transaction_count,
            COALESCE(ABS(SUM(t.amount_cents)), 0) AS total_spent_cents
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND t.amount_cents < 0
          AND COALESCE(c.name, 'Uncategorized') = 'Uncategorized'
        """,
        (period_start, next_day(period_end)),
    ).fetchone()
    prior_totals = {
        str(row["category_name"]): int(row["total_spent_cents"])
        for row in conn.execute(
            """
            SELECT
                COALESCE(c.name, 'Uncategorized') AS category_name,
                COALESCE(ABS(SUM(t.amount_cents)), 0) AS total_spent_cents
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.posted_at >= ? AND t.posted_at < ?
              AND t.amount_cents < 0
            GROUP BY COALESCE(c.name, 'Uncategorized')
            """,
            (prior_start, next_day(prior_end)),
        ).fetchall()
    }
    comparison_rows = [
        {
            "category_name": category_name,
            "current_total_spent": cents_to_display_dollars(current_totals.get(category_name, 0)),
            "prior_total_spent": cents_to_display_dollars(prior_totals.get(category_name, 0)),
            "delta_spent": cents_to_display_dollars(
                current_totals.get(category_name, 0) - prior_totals.get(category_name, 0)
            ),
        }
        for category_name in sorted(
            set(current_totals) | set(prior_totals),
            key=lambda name: (
                -(abs(current_totals.get(name, 0) - prior_totals.get(name, 0))),
                name,
            ),
        )
    ]
    return {
        "top_categories": [
            {
                "category_name": str(row["category_name"]),
                "total_spent": cents_to_display_dollars(int(row["total_spent_cents"])),
            }
            for row in current_rows
        ],
        "top_merchants": [
            {
                "merchant": str(row["merchant"]),
                "total_spent": cents_to_display_dollars(int(row["total_spent_cents"])),
                "transaction_count": int(row["transaction_count"]),
            }
            for row in merchant_rows
        ],
        "income_patterns": [
            {
                "merchant": str(row["merchant"]),
                "transaction_count": int(row["transaction_count"]),
                "total_income": cents_to_display_dollars(int(row["total_income_cents"])),
            }
            for row in income_rows
        ],
        "uncategorized_summary": {
            "transaction_count": int(uncategorized_row["transaction_count"]),
            "total_spent": cents_to_display_dollars(int(uncategorized_row["total_spent_cents"])),
        },
        "changes_vs_prior_period": comparison_rows,
    }


def sensitive_query_total_cents(
    conn: Connection,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    category_name: str | None = None,
    merchant: str | None = None,
    account_name: str | None = None,
    description_contains: str | None = None,
    session_ref: str | None = None,
) -> int:
    """Return total spending (outflow) cents for the filtered window.

    Only transactions with ``amount_cents < 0`` (outflows) are included. The
    result is returned as a non-negative integer (absolute value of the sum).
    Companion to :func:`sensitive_query_count`, which applies the same
    direction filter so the pair always answers ``"how much was spent and
    across how many transactions?"`` consistently.
    """
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
    # Safe: WHERE joins sensitive-filter templates (? only) plus literal outflow predicate; params bound.
    row = conn.execute(
        f"""
        SELECT COALESCE(ABS(SUM(t.amount_cents)), 0) AS total_cents
        FROM finance_transactions t
        JOIN finance_accounts a ON a.id = t.account_id
        LEFT JOIN finance_categories c ON c.id = t.category_id
        {where_clause}
        """,  # noqa: S608
        params,
    ).fetchone()
    log_sensitive_access(conn, "finance_query", session_ref, "aggregate intent=sum_spending")
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
    session_ref: str | None = None,
) -> int:
    """Return the count of matching spending transactions (outflows only).

    Mirrors :func:`sensitive_query_total_cents`: only ``amount_cents < 0``
    rows are counted so the count is consistent with the spending total for
    the same filters. This also aligns with
    :meth:`minx_mcp.finance.read_api.FinanceReadAPI.get_filtered_transaction_count`,
    which applies the same outflow filter via
    ``_build_filtered_expense_query``. Historically this function counted all
    transactions (including credits/refunds), which disagreed with the
    spending total for the same inputs — callers asking "how many Dining Out
    transactions in this window?" would see refunds inflate the count while
    the companion total excluded them.
    """
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
    # Safe: same dynamic WHERE as sensitive aggregates; only ? placeholders carry user filters.
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total_count
        FROM finance_transactions t
        JOIN finance_accounts a ON a.id = t.account_id
        LEFT JOIN finance_categories c ON c.id = t.category_id
        {where_clause}
        """,  # noqa: S608
        params,
    ).fetchone()
    log_sensitive_access(
        conn, "finance_query", session_ref, "aggregate intent=count_spending_transactions"
    )
    return int(row["total_count"])


def _prior_period_window(period_start: str, period_end: str) -> tuple[str, str]:
    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    span_days = (end - start).days + 1
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=span_days - 1)
    return prior_start.isoformat(), prior_end.isoformat()


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
        params.append(next_day(end_date))
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
