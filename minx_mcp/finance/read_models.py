from __future__ import annotations

from dataclasses import dataclass


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
class IncomeSource:
    name: str
    total_cents: int
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
class IncomeSummary:
    total_income_cents: int
    by_source: list[IncomeSource]


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


__all__ = [
    "CategoryDelta",
    "CategorySpending",
    "ImportJobIssue",
    "IncomeSource",
    "IncomeSummary",
    "MerchantSpending",
    "PeriodComparison",
    "SpendingSummary",
    "UncategorizedSummary",
]
