"""SQL-backed aggregation and markdown rendering for finance report summaries.

This module owns both the builder stage (SQL-backed aggregation that turns
rows into the dataclasses defined in :mod:`minx_mcp.finance.report_models`)
and the renderer stage (markdown assembly from those dataclasses). They live
together because the rendered markdown is tightly coupled to the builder
output shape: adding a field in a builder almost always requires a template
change, and adding a section in a template almost always requires new
builder data. Splitting them would force every change to cross module
boundaries without any abstraction benefit.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from sqlite3 import Connection
from string import Template
from typing import Any, TypeVar

from minx_mcp.finance.report_models import (
    AccountRollup,
    AnomalyItem,
    CategoryTotal,
    MoneyTotals,
    MonthlyChange,
    MonthlyReportSummary,
    MonthlyReviewItem,
    NewMerchantReviewItem,
    NotableMerchant,
    RecurringChargeHighlight,
    TopCategory,
    UncategorizedReviewItem,
    UncategorizedTransaction,
    WeeklyCategoryChange,
    WeeklyReportSummary,
)
from minx_mcp.money import format_cents
from minx_mcp.preferences import get_finance_anomaly_threshold_cents
from minx_mcp.time_utils import next_day

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

T = TypeVar("T")


def _large_uncategorized_anomaly_rows(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> list[Any]:
    threshold = get_finance_anomaly_threshold_cents(conn)
    return list(
        conn.execute(
            """
            SELECT t.id, t.posted_at, t.description, t.amount_cents
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.amount_cents <= ?
              AND COALESCE(c.name, 'Uncategorized') = 'Uncategorized'
              AND t.posted_at >= ? AND t.posted_at < ?
            ORDER BY t.amount_cents ASC, t.id ASC
            """,
            (threshold, period_start, end_exclusive),
        ).fetchall()
    )


def _uncategorized_transaction_rows(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> list[Any]:
    return list(
        conn.execute(
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
    )


def _anomaly_items_from_rows(rows: Sequence[Any]) -> list[AnomalyItem]:
    return [
        AnomalyItem(
            kind="large_uncategorized",
            transaction_id=int(row["id"]),
            posted_at=str(row["posted_at"]),
            description=str(row["description"]),
            amount_cents=int(row["amount_cents"]),
        )
        for row in rows
    ]


def _uncategorized_transactions_from_rows(rows: Sequence[Any]) -> list[UncategorizedTransaction]:
    return [
        UncategorizedTransaction(
            id=int(row["id"]),
            posted_at=str(row["posted_at"]),
            description=str(row["description"]),
            amount_cents=int(row["amount_cents"]),
        )
        for row in rows
    ]


def build_weekly_report(
    conn: Connection,
    period_start: str,
    period_end: str,
) -> WeeklyReportSummary:
    end_exclusive = next_day(period_end)
    prior_start, prior_end = _previous_window(period_start, period_end)
    prior_end_exclusive = next_day(prior_end)
    totals_row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents END), 0) AS inflow_cents,
            COALESCE(ABS(SUM(CASE WHEN amount_cents < 0 THEN amount_cents END)), 0) AS outflow_cents
        FROM finance_transactions
        WHERE posted_at >= ? AND posted_at < ?
        """,
        (period_start, end_exclusive),
    ).fetchone()
    totals = MoneyTotals(
        inflow_cents=int(totals_row["inflow_cents"]),
        outflow_cents=int(totals_row["outflow_cents"]),
    )
    top_categories = [
        TopCategory(
            category_name=str(row["category_name"]),
            total_outflow_cents=int(row["total_outflow_cents"]),
        )
        for row in conn.execute(
            """
            SELECT
                COALESCE(c.name, 'Uncategorized') AS category_name,
                COALESCE(ABS(SUM(t.amount_cents)), 0) AS total_outflow_cents
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.posted_at >= ? AND t.posted_at < ? AND t.amount_cents < 0
            GROUP BY COALESCE(c.name, 'Uncategorized')
            ORDER BY total_outflow_cents DESC, category_name ASC
            LIMIT 5
            """,
            (period_start, end_exclusive),
        ).fetchall()
    ]
    notable_merchants = [
        NotableMerchant(
            merchant=str(row["merchant"]),
            total_outflow_cents=int(row["total_outflow_cents"]),
            transaction_count=int(row["transaction_count"]),
        )
        for row in conn.execute(
            """
            SELECT
                merchant,
                COALESCE(ABS(SUM(amount_cents)), 0) AS total_outflow_cents,
                COUNT(*) AS transaction_count
            FROM finance_transactions
            WHERE posted_at >= ? AND posted_at < ?
              AND amount_cents < 0
              AND COALESCE(merchant, '') != ''
            GROUP BY merchant
            ORDER BY total_outflow_cents DESC, merchant ASC
            LIMIT 5
            """,
            (period_start, end_exclusive),
        ).fetchall()
    ]
    current_categories = _category_outflow_map(conn, period_start, end_exclusive)
    prior_categories = _category_outflow_map(conn, prior_start, prior_end_exclusive)
    category_changes = [
        WeeklyCategoryChange(
            category_name=category_name,
            current_outflow_cents=current_categories.get(category_name, 0),
            prior_outflow_cents=prior_categories.get(category_name, 0),
            delta_outflow_cents=current_categories.get(category_name, 0)
            - prior_categories.get(category_name, 0),
        )
        for category_name in sorted(set(current_categories) | set(prior_categories))
    ]
    category_changes.sort(key=lambda item: (-abs(item.delta_outflow_cents), item.category_name))

    unc_rows = _uncategorized_transaction_rows(conn, period_start, end_exclusive)
    return WeeklyReportSummary(
        period_start=period_start,
        period_end=period_end,
        totals=totals,
        top_categories=top_categories,
        notable_merchants=notable_merchants,
        category_changes=category_changes,
        anomalies=_anomaly_items_from_rows(
            _large_uncategorized_anomaly_rows(conn, period_start, end_exclusive)
        ),
        uncategorized_transactions=_uncategorized_transactions_from_rows(unc_rows),
    )


def build_monthly_report(
    conn: Connection,
    period_start: str,
    period_end: str,
) -> MonthlyReportSummary:
    end_exclusive = next_day(period_end)
    prior_start, prior_end = _previous_month_window(period_start)
    prior_end_exclusive = next_day(prior_end)
    account_rollups = [
        AccountRollup(
            account_name=str(row["account_name"]),
            total_amount_cents=int(row["total_amount_cents"]),
        )
        for row in conn.execute(
            """
            SELECT a.name AS account_name, COALESCE(SUM(t.amount_cents), 0) AS total_amount_cents
            FROM finance_transactions t
            JOIN finance_accounts a ON a.id = t.account_id
            WHERE t.posted_at >= ? AND t.posted_at < ?
            GROUP BY a.name
            ORDER BY total_amount_cents ASC, account_name ASC
            """,
            (period_start, end_exclusive),
        ).fetchall()
    ]
    category_totals = [
        CategoryTotal(
            category_name=str(row["category_name"]),
            total_amount_cents=int(row["total_amount_cents"]),
        )
        for row in conn.execute(
            """
            SELECT
                COALESCE(c.name, 'Uncategorized') AS category_name,
                COALESCE(SUM(t.amount_cents), 0) AS total_amount_cents
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.posted_at >= ? AND t.posted_at < ?
            GROUP BY COALESCE(c.name, 'Uncategorized')
            ORDER BY total_amount_cents ASC, category_name ASC
            """,
            (period_start, end_exclusive),
        ).fetchall()
    ]
    current_accounts = _account_total_map(conn, period_start, end_exclusive)
    prior_accounts = _account_total_map(conn, prior_start, prior_end_exclusive)
    changes_vs_prior_month = [
        MonthlyChange(
            account_name=account_name,
            current_total_cents=current_accounts.get(account_name, 0),
            prior_total_cents=prior_accounts.get(account_name, 0),
            delta_total_cents=current_accounts.get(account_name, 0)
            - prior_accounts.get(account_name, 0),
        )
        for account_name in sorted(set(current_accounts) | set(prior_accounts))
    ]
    recurring_charge_highlights = _recurring_charge_highlights(
        conn,
        period_start,
        end_exclusive,
        prior_start,
        prior_end_exclusive,
    )

    return MonthlyReportSummary(
        period_start=period_start,
        period_end=period_end,
        account_rollups=account_rollups,
        category_totals=category_totals,
        changes_vs_prior_month=changes_vs_prior_month,
        recurring_charge_highlights=recurring_charge_highlights,
        anomalies=_anomaly_items_from_rows(
            _large_uncategorized_anomaly_rows(conn, period_start, end_exclusive)
        ),
        uncategorized_or_new_merchants=_monthly_review_items(
            conn,
            period_start,
            end_exclusive,
        ),
    )


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


def _category_outflow_map(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT
            COALESCE(c.name, 'Uncategorized') AS category_name,
            COALESCE(ABS(SUM(t.amount_cents)), 0) AS total_outflow_cents
        FROM finance_transactions t
        LEFT JOIN finance_categories c ON c.id = t.category_id
        WHERE t.posted_at >= ? AND t.posted_at < ? AND t.amount_cents < 0
        GROUP BY COALESCE(c.name, 'Uncategorized')
        """,
        (period_start, end_exclusive),
    ).fetchall()
    return {str(row["category_name"]): int(row["total_outflow_cents"]) for row in rows}


def _account_total_map(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT a.name AS account_name, COALESCE(SUM(t.amount_cents), 0) AS total_amount_cents
        FROM finance_transactions t
        JOIN finance_accounts a ON a.id = t.account_id
        WHERE t.posted_at >= ? AND t.posted_at < ?
        GROUP BY a.name
        """,
        (period_start, end_exclusive),
    ).fetchall()
    return {str(row["account_name"]): int(row["total_amount_cents"]) for row in rows}


def _recurring_charge_highlights(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
    prior_start: str,
    prior_end_exclusive: str,
) -> list[RecurringChargeHighlight]:
    current = _merchant_outflow_map(conn, period_start, end_exclusive)
    prior = _merchant_outflow_map(conn, prior_start, prior_end_exclusive)

    highlights = [
        RecurringChargeHighlight(
            merchant=merchant,
            current_outflow_cents=current[merchant][1],
            prior_outflow_cents=prior[merchant][1],
            current_count=current[merchant][0],
            prior_count=prior[merchant][0],
        )
        for merchant in sorted(set(current) & set(prior))
    ]
    highlights.sort(key=lambda item: (-item.current_outflow_cents, item.merchant))
    return highlights


def _merchant_outflow_map(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> dict[str, tuple[int, int]]:
    rows = conn.execute(
        """
        SELECT merchant, COUNT(*) AS transaction_count, COALESCE(ABS(SUM(amount_cents)), 0) AS total_outflow_cents
        FROM finance_transactions
        WHERE posted_at >= ? AND posted_at < ?
          AND amount_cents < 0
          AND COALESCE(merchant, '') != ''
        GROUP BY merchant
        """,
        (period_start, end_exclusive),
    ).fetchall()
    return {
        str(row["merchant"]): (
            int(row["transaction_count"]),
            int(row["total_outflow_cents"]),
        )
        for row in rows
    }


def _monthly_review_items(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> list[MonthlyReviewItem]:
    items: list[MonthlyReviewItem] = [
        UncategorizedReviewItem(
            posted_at=str(row["posted_at"]),
            description=str(row["description"]),
            amount_cents=int(row["amount_cents"]),
        )
        for row in _uncategorized_transaction_rows(conn, period_start, end_exclusive)
    ]

    new_merchants = conn.execute(
        """
        SELECT
            t.merchant,
            MIN(t.posted_at) AS first_seen_at,
            COALESCE(SUM(t.amount_cents), 0) AS total_amount_cents
        FROM finance_transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND COALESCE(t.merchant, '') != ''
          AND NOT EXISTS (
              SELECT 1
              FROM finance_transactions earlier
              WHERE earlier.merchant = t.merchant
                AND earlier.posted_at < ?
          )
        GROUP BY t.merchant
        ORDER BY total_amount_cents ASC, t.merchant ASC
        """,
        (period_start, end_exclusive, period_start),
    ).fetchall()
    items.extend(
        NewMerchantReviewItem(
            merchant=str(row["merchant"]),
            first_seen_at=str(row["first_seen_at"]),
            total_amount_cents=int(row["total_amount_cents"]),
        )
        for row in new_merchants
    )
    return items


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_weekly_markdown(
    summary: WeeklyReportSummary,
    period_start: str,
    period_end: str,
) -> str:
    template = Template((TEMPLATE_DIR / "finance-weekly-summary.md").read_text(encoding="utf-8"))
    return _render(
        template,
        period_start=period_start,
        period_end=period_end,
        inflow=_fmt_cents(summary.totals.inflow_cents),
        outflow=_fmt_cents(summary.totals.outflow_cents),
        top_category_lines=_lines(
            summary.top_categories,
            lambda i: f"- {i.category_name}: {_fmt_cents(i.total_outflow_cents)}",
        ),
        merchant_lines=_lines(
            summary.notable_merchants,
            lambda i: (
                f"- {i.merchant}: {_fmt_cents(i.total_outflow_cents)} across "
                f"{i.transaction_count} transaction(s)"
            ),
        ),
        category_change_lines=_lines(
            summary.category_changes,
            lambda i: (
                f"- {i.category_name}: current {_fmt_cents(i.current_outflow_cents)}, "
                f"prior {_fmt_cents(i.prior_outflow_cents)}, "
                f"delta {_fmt_cents(i.delta_outflow_cents)}"
            ),
        ),
        anomaly_lines=_lines(
            summary.anomalies,
            lambda i: f"- {i.description}: {_fmt_cents(i.amount_cents)}",
        ),
        uncategorized_lines=_lines(
            summary.uncategorized_transactions,
            lambda i: f"- {i.posted_at} {i.description}: {_fmt_cents(i.amount_cents)}",
        ),
    )


def render_monthly_markdown(
    summary: MonthlyReportSummary,
    period_start: str,
    period_end: str,
) -> str:
    template = Template((TEMPLATE_DIR / "finance-monthly-summary.md").read_text(encoding="utf-8"))
    return _render(
        template,
        period_start=period_start,
        period_end=period_end,
        account_rollup_lines=_lines(
            summary.account_rollups,
            lambda i: f"- {i.account_name}: {_fmt_cents(i.total_amount_cents)}",
        ),
        category_lines=_lines(
            summary.category_totals,
            lambda i: f"- {i.category_name}: {_fmt_cents(i.total_amount_cents)}",
        ),
        change_lines=_lines(
            summary.changes_vs_prior_month,
            lambda i: (
                f"- {i.account_name}: current {_fmt_cents(i.current_total_cents)}, "
                f"prior {_fmt_cents(i.prior_total_cents)}, "
                f"delta {_fmt_cents(i.delta_total_cents)}"
            ),
        ),
        recurring_lines=_lines(
            summary.recurring_charge_highlights,
            lambda i: (
                f"- {i.merchant}: current {_fmt_cents(i.current_outflow_cents)}, "
                f"prior {_fmt_cents(i.prior_outflow_cents)}"
            ),
        ),
        anomaly_lines=_lines(
            summary.anomalies,
            lambda i: f"- {i.description}: {_fmt_cents(i.amount_cents)}",
        ),
        review_lines=_lines(
            summary.uncategorized_or_new_merchants,
            _fmt_review_item,
        ),
    )


def _render(template: Template, **kwargs: object) -> str:
    try:
        return template.substitute(**kwargs)
    except KeyError as exc:
        raise ValueError(f"Template has unresolved placeholders: {exc.args[0]}") from exc


def _fmt_cents(cents: int) -> str:
    return format_cents(cents)


def _lines[T](items: Sequence[T], render: Callable[[T], str]) -> str:
    if not items:
        return "- None"
    return "\n".join(render(item) for item in items)


def _fmt_review_item(item: MonthlyReviewItem) -> str:
    if isinstance(item, NewMerchantReviewItem):
        return (
            f"- new merchant {item.merchant} first seen {item.first_seen_at}: "
            f"{_fmt_cents(item.total_amount_cents)}"
        )
    return f"- uncategorized {item.posted_at} {item.description}: {_fmt_cents(item.amount_cents)}"
