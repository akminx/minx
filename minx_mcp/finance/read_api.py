from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from sqlite3 import Connection

from minx_mcp.jobs import STUCK_JOB_TIMEOUT_MINUTES


@dataclass(frozen=True)
class CategorySpending:
    category_name: str
    total_spent_cents: int


@dataclass(frozen=True)
class MerchantSpending:
    merchant: str
    total_spent_cents: int
    transaction_count: int


@dataclass(frozen=True)
class SpendingSummary:
    total_spent_cents: int
    by_category: list[CategorySpending]
    top_merchants: list[MerchantSpending]


@dataclass(frozen=True)
class UncategorizedSummary:
    transaction_count: int
    total_spent_cents: int


@dataclass(frozen=True)
class ImportJobIssue:
    job_id: str
    issue_kind: str
    status: str
    source_ref: str | None
    updated_at: str
    error_message: str | None


@dataclass(frozen=True)
class CategoryDelta:
    category_name: str
    current_total_spent_cents: int
    prior_total_spent_cents: int
    delta_spent_cents: int


@dataclass(frozen=True)
class PeriodComparison:
    current_total_spent_cents: int
    prior_total_spent_cents: int
    category_deltas: list[CategoryDelta]


class FinanceReadAPI:
    """Read-only interface for Minx Core to query Finance domain data."""

    def __init__(self, db: Connection):
        self._db = db

    def get_spending_summary(self, start_date: str, end_date: str) -> SpendingSummary:
        end_exclusive = _next_day(end_date)
        total_row = self._db.execute(
            """
            SELECT COALESCE(ABS(SUM(CASE WHEN amount_cents < 0 THEN amount_cents END)), 0) AS total_spent_cents
            FROM finance_transactions
            WHERE posted_at >= ? AND posted_at < ?
            """,
            (start_date, end_exclusive),
        ).fetchone()
        by_category = [
            CategorySpending(
                category_name=str(row["category_name"]),
                total_spent_cents=int(row["total_spent_cents"]),
            )
            for row in self._db.execute(
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
                (start_date, end_exclusive),
            ).fetchall()
        ]
        top_merchants = [
            MerchantSpending(
                merchant=str(row["merchant"]),
                total_spent_cents=int(row["total_spent_cents"]),
                transaction_count=int(row["transaction_count"]),
            )
            for row in self._db.execute(
                """
                SELECT
                    merchant,
                    COALESCE(ABS(SUM(amount_cents)), 0) AS total_spent_cents,
                    COUNT(*) AS transaction_count
                FROM finance_transactions
                WHERE posted_at >= ? AND posted_at < ?
                  AND amount_cents < 0
                  AND COALESCE(merchant, '') != ''
                GROUP BY merchant
                ORDER BY total_spent_cents DESC, merchant ASC
                """,
                (start_date, end_exclusive),
            ).fetchall()
        ]
        return SpendingSummary(
            total_spent_cents=int(total_row["total_spent_cents"]),
            by_category=by_category,
            top_merchants=top_merchants,
        )

    def get_uncategorized(self, start_date: str, end_date: str) -> UncategorizedSummary:
        end_exclusive = _next_day(end_date)
        row = self._db.execute(
            """
            SELECT
                COUNT(*) AS transaction_count,
                COALESCE(ABS(SUM(CASE WHEN t.amount_cents < 0 THEN t.amount_cents END)), 0) AS total_spent_cents
            FROM finance_transactions t
            LEFT JOIN finance_categories c ON c.id = t.category_id
            WHERE t.posted_at >= ? AND t.posted_at < ?
              AND COALESCE(c.name, 'Uncategorized') = 'Uncategorized'
            """,
            (start_date, end_exclusive),
        ).fetchone()
        return UncategorizedSummary(
            transaction_count=int(row["transaction_count"]),
            total_spent_cents=int(row["total_spent_cents"]),
        )

    def get_import_job_issues(self) -> list[ImportJobIssue]:
        rows = self._db.execute(
            """
            SELECT
                id AS job_id,
                CASE
                    WHEN status = 'failed' THEN 'failed'
                    ELSE 'stale'
                END AS issue_kind,
                status,
                source_ref,
                updated_at,
                error_message
            FROM jobs
            WHERE job_type = 'finance_import'
              AND (
                  status = 'failed'
                  OR (status = 'running' AND updated_at < datetime('now', ?))
              )
            ORDER BY
                CASE
                    WHEN status = 'failed' THEN 0
                    ELSE 1
                END ASC,
                updated_at ASC,
                job_id ASC
            """,
            (f"-{STUCK_JOB_TIMEOUT_MINUTES} minutes",),
        ).fetchall()
        return [
            ImportJobIssue(
                job_id=str(row["job_id"]),
                issue_kind=str(row["issue_kind"]),
                status=str(row["status"]),
                source_ref=row["source_ref"],
                updated_at=str(row["updated_at"]),
                error_message=row["error_message"],
            )
            for row in rows
        ]

    def get_period_comparison(
        self,
        current_start: str,
        current_end: str,
        prior_start: str,
        prior_end: str,
    ) -> PeriodComparison:
        current_end_exclusive = _next_day(current_end)
        prior_end_exclusive = _next_day(prior_end)
        current_total = _read_total_spent_cents(self._db, current_start, current_end_exclusive)
        prior_total = _read_total_spent_cents(self._db, prior_start, prior_end_exclusive)
        current_by_category = _read_category_spend_map(self._db, current_start, current_end_exclusive)
        prior_by_category = _read_category_spend_map(self._db, prior_start, prior_end_exclusive)
        category_deltas = [
            CategoryDelta(
                category_name=category_name,
                current_total_spent_cents=current_by_category.get(category_name, 0),
                prior_total_spent_cents=prior_by_category.get(category_name, 0),
                delta_spent_cents=current_by_category.get(category_name, 0)
                - prior_by_category.get(category_name, 0),
            )
            for category_name in set(current_by_category) | set(prior_by_category)
        ]
        category_deltas.sort(
            key=lambda item: (-abs(item.delta_spent_cents), item.category_name)
        )
        return PeriodComparison(
            current_total_spent_cents=current_total,
            prior_total_spent_cents=prior_total,
            category_deltas=category_deltas,
        )


def _read_total_spent_cents(db: Connection, start_date: str, end_exclusive: str) -> int:
    row = db.execute(
        """
        SELECT COALESCE(ABS(SUM(CASE WHEN amount_cents < 0 THEN amount_cents END)), 0) AS total_spent_cents
        FROM finance_transactions
        WHERE posted_at >= ? AND posted_at < ?
        """,
        (start_date, end_exclusive),
    ).fetchone()
    return int(row["total_spent_cents"])


def _read_category_spend_map(db: Connection, start_date: str, end_exclusive: str) -> dict[str, int]:
    rows = db.execute(
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
        (start_date, end_exclusive),
    ).fetchall()
    return {
        str(row["category_name"]): int(row["total_spent_cents"])
        for row in rows
    }


def _next_day(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()
