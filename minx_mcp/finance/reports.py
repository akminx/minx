from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from sqlite3 import Connection
from string import Template

from minx_mcp.finance.analytics import find_anomalies, find_uncategorized
from minx_mcp.finance.report_models import (
    AccountRollup,
    AnomalyItem,
    CategoryTotal,
    MonthlyChange,
    MonthlyReportSummary,
    MonthlyReviewItem,
    MoneyTotals,
    NewMerchantReviewItem,
    NotableMerchant,
    RecurringChargeHighlight,
    TopCategory,
    UncategorizedReviewItem,
    UncategorizedTransaction,
    WeeklyCategoryChange,
    WeeklyReportSummary,
)
from minx_mcp.money import cents_to_dollars

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"


def build_weekly_report(
    conn: Connection,
    period_start: str,
    period_end: str,
) -> WeeklyReportSummary:
    end_exclusive = _next_day(period_end)
    prior_start, prior_end = _previous_window(period_start, period_end)
    prior_end_exclusive = _next_day(prior_end)
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
        inflow=cents_to_dollars(int(totals_row["inflow_cents"])),
        outflow=cents_to_dollars(int(totals_row["outflow_cents"])),
    )
    top_categories = [
        TopCategory(
            category_name=str(row["category_name"]),
            total_outflow=cents_to_dollars(int(row["total_outflow_cents"])),
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
            total_outflow=cents_to_dollars(int(row["total_outflow_cents"])),
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
            current_outflow=current_categories.get(category_name, 0.0),
            prior_outflow=prior_categories.get(category_name, 0.0),
            delta_outflow=round(
                current_categories.get(category_name, 0.0)
                - prior_categories.get(category_name, 0.0),
                2,
            ),
        )
        for category_name in sorted(set(current_categories) | set(prior_categories))
    ]
    category_changes.sort(
        key=lambda item: (-abs(item.delta_outflow), item.category_name)
    )

    return WeeklyReportSummary(
        period_start=period_start,
        period_end=period_end,
        totals=totals,
        top_categories=top_categories,
        notable_merchants=notable_merchants,
        category_changes=category_changes,
        anomalies=_anomaly_items(find_anomalies(conn, period_start, end_exclusive)),
        uncategorized_transactions=_uncategorized_transactions(
            find_uncategorized(conn, period_start, end_exclusive)
        ),
    )


def build_monthly_report(
    conn: Connection,
    period_start: str,
    period_end: str,
) -> MonthlyReportSummary:
    end_exclusive = _next_day(period_end)
    prior_start, prior_end = _previous_month_window(period_start)
    prior_end_exclusive = _next_day(prior_end)
    account_rollups = [
        AccountRollup(
            account_name=str(row["account_name"]),
            total_amount=cents_to_dollars(int(row["total_amount_cents"])),
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
            total_amount=cents_to_dollars(int(row["total_amount_cents"])),
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
            current_total=current_accounts.get(account_name, 0.0),
            prior_total=prior_accounts.get(account_name, 0.0),
            delta_total=round(
                current_accounts.get(account_name, 0.0)
                - prior_accounts.get(account_name, 0.0),
                2,
            ),
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
        anomalies=_anomaly_items(find_anomalies(conn, period_start, end_exclusive)),
        uncategorized_or_new_merchants=_monthly_review_items(
            conn,
            period_start,
            end_exclusive,
        ),
    )


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


def _next_day(d: str) -> str:
    return (date.fromisoformat(d) + timedelta(days=1)).isoformat()


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
) -> dict[str, float]:
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
    return {
        str(row["category_name"]): cents_to_dollars(int(row["total_outflow_cents"]))
        for row in rows
    }


def _account_total_map(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> dict[str, float]:
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
    return {
        str(row["account_name"]): cents_to_dollars(int(row["total_amount_cents"]))
        for row in rows
    }


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
            current_outflow=cents_to_dollars(int(current[merchant]["total_outflow_cents"])),
            prior_outflow=cents_to_dollars(int(prior[merchant]["total_outflow_cents"])),
            current_count=int(current[merchant]["transaction_count"]),
            prior_count=int(prior[merchant]["transaction_count"]),
        )
        for merchant in sorted(set(current) & set(prior))
    ]
    highlights.sort(key=lambda item: (-item.current_outflow, item.merchant))
    return highlights


def _merchant_outflow_map(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> dict[str, dict[str, object]]:
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
    return {str(row["merchant"]): dict(row) for row in rows}


def _monthly_review_items(
    conn: Connection,
    period_start: str,
    end_exclusive: str,
) -> list[MonthlyReviewItem]:
    items: list[MonthlyReviewItem] = []
    for row in find_uncategorized(conn, period_start, end_exclusive):
        items.append(
            UncategorizedReviewItem(
                posted_at=str(row["posted_at"]),
                description=str(row["description"]),
                amount=float(row["amount"]),
            )
        )

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
    for row in new_merchants:
        items.append(
            NewMerchantReviewItem(
                merchant=str(row["merchant"]),
                first_seen_at=str(row["first_seen_at"]),
                total_amount=cents_to_dollars(int(row["total_amount_cents"])),
            )
        )
    return items


def render_weekly_markdown(
    summary: WeeklyReportSummary,
    period_start: str,
    period_end: str,
) -> str:
    template = Template((TEMPLATE_DIR / "finance-weekly-summary.md").read_text())
    return _render(
        template,
        period_start=period_start,
        period_end=period_end,
        inflow=_fmt(summary.totals.inflow),
        outflow=_fmt(summary.totals.outflow),
        top_category_lines=_lines(
            summary.top_categories,
            lambda i: f"- {i.category_name}: {_fmt(i.total_outflow)}",
        ),
        merchant_lines=_lines(
            summary.notable_merchants,
            lambda i: (
                f"- {i.merchant}: {_fmt(i.total_outflow)} across "
                f"{i.transaction_count} transaction(s)"
            ),
        ),
        category_change_lines=_lines(
            summary.category_changes,
            lambda i: (
                f"- {i.category_name}: current {_fmt(i.current_outflow)}, "
                f"prior {_fmt(i.prior_outflow)}, delta {_fmt(i.delta_outflow)}"
            ),
        ),
        anomaly_lines=_lines(
            summary.anomalies,
            lambda i: f"- {i.description}: {_fmt(i.amount)}",
        ),
        uncategorized_lines=_lines(
            summary.uncategorized_transactions,
            lambda i: f"- {i.posted_at} {i.description}: {_fmt(i.amount)}",
        ),
    )


def render_monthly_markdown(
    summary: MonthlyReportSummary,
    period_start: str,
    period_end: str,
) -> str:
    template = Template((TEMPLATE_DIR / "finance-monthly-summary.md").read_text())
    return _render(
        template,
        period_start=period_start,
        period_end=period_end,
        account_rollup_lines=_lines(
            summary.account_rollups,
            lambda i: f"- {i.account_name}: {_fmt(i.total_amount)}",
        ),
        category_lines=_lines(
            summary.category_totals,
            lambda i: f"- {i.category_name}: {_fmt(i.total_amount)}",
        ),
        change_lines=_lines(
            summary.changes_vs_prior_month,
            lambda i: (
                f"- {i.account_name}: current {_fmt(i.current_total)}, "
                f"prior {_fmt(i.prior_total)}, delta {_fmt(i.delta_total)}"
            ),
        ),
        recurring_lines=_lines(
            summary.recurring_charge_highlights,
            lambda i: (
                f"- {i.merchant}: current {_fmt(i.current_outflow)}, "
                f"prior {_fmt(i.prior_outflow)}"
            ),
        ),
        anomaly_lines=_lines(
            summary.anomalies,
            lambda i: f"- {i.description}: {_fmt(i.amount)}",
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


def _fmt(value: object) -> str:
    amount = float(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):.2f}"


def _lines(items: list[object], render) -> str:
    if not items:
        return "- None"
    return "\n".join(render(item) for item in items)


def _fmt_review_item(item: MonthlyReviewItem) -> str:
    if isinstance(item, NewMerchantReviewItem):
        return (
            f"- new merchant {item.merchant} first seen {item.first_seen_at}: "
            f"{_fmt(item.total_amount)}"
        )
    return (
        f"- uncategorized {item.posted_at} {item.description}: "
        f"{_fmt(item.amount)}"
    )


def _anomaly_items(items: list[dict[str, object]]) -> list[AnomalyItem]:
    return [
        AnomalyItem(
            kind=str(item["kind"]),
            transaction_id=(
                int(item["transaction_id"])
                if item.get("transaction_id") is not None
                else None
            ),
            posted_at=str(item["posted_at"]),
            description=str(item["description"]),
            amount=float(item["amount"]),
        )
        for item in items
    ]


def _uncategorized_transactions(
    items: list[dict[str, object]],
) -> list[UncategorizedTransaction]:
    return [
        UncategorizedTransaction(
            id=int(item["id"]) if item.get("id") is not None else None,
            posted_at=str(item["posted_at"]),
            description=str(item["description"]),
            amount=float(item["amount"]),
        )
        for item in items
    ]
