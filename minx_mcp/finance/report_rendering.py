"""Load markdown templates and render finance report summaries."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from string import Template
from typing import SupportsFloat, TypeVar

from minx_mcp.finance.report_models import (
    MonthlyReportSummary,
    MonthlyReviewItem,
    NewMerchantReviewItem,
    WeeklyReportSummary,
)

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"

T = TypeVar("T")


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


def _fmt(value: SupportsFloat) -> str:
    amount = float(value)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):.2f}"


def _lines(items: Sequence[T], render: Callable[[T], str]) -> str:
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
