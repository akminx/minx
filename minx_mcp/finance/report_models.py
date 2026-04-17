from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MoneyTotals:
    inflow: float
    outflow: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class TopCategory:
    category_name: str
    total_outflow: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class NotableMerchant:
    merchant: str
    total_outflow: float
    transaction_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class WeeklyCategoryChange:
    category_name: str
    current_outflow: float
    prior_outflow: float
    delta_outflow: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AnomalyItem:
    kind: str
    transaction_id: int | None
    posted_at: str
    description: str
    amount: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class UncategorizedTransaction:
    id: int | None
    posted_at: str
    description: str
    amount: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class WeeklyReportSummary:
    period_start: str
    period_end: str
    totals: MoneyTotals
    top_categories: list[TopCategory]
    notable_merchants: list[NotableMerchant]
    category_changes: list[WeeklyCategoryChange]
    anomalies: list[AnomalyItem]
    uncategorized_transactions: list[UncategorizedTransaction]

    def to_dict(self) -> dict[str, object]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "totals": self.totals.to_dict(),
            "top_categories": [item.to_dict() for item in self.top_categories],
            "notable_merchants": [item.to_dict() for item in self.notable_merchants],
            "category_changes": [item.to_dict() for item in self.category_changes],
            "anomalies": [item.to_dict() for item in self.anomalies],
            "uncategorized_transactions": [
                item.to_dict() for item in self.uncategorized_transactions
            ],
        }


@dataclass(frozen=True)
class AccountRollup:
    account_name: str
    total_amount: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CategoryTotal:
    category_name: str
    total_amount: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MonthlyChange:
    account_name: str
    current_total: float
    prior_total: float
    delta_total: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RecurringChargeHighlight:
    merchant: str
    current_outflow: float
    prior_outflow: float
    current_count: int
    prior_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class NewMerchantReviewItem:
    merchant: str
    first_seen_at: str
    total_amount: float
    kind: str = "new_merchant"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class UncategorizedReviewItem:
    posted_at: str
    description: str
    amount: float
    kind: str = "uncategorized_transaction"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


MonthlyReviewItem = NewMerchantReviewItem | UncategorizedReviewItem


@dataclass(frozen=True)
class MonthlyReportSummary:
    period_start: str
    period_end: str
    account_rollups: list[AccountRollup]
    category_totals: list[CategoryTotal]
    changes_vs_prior_month: list[MonthlyChange]
    recurring_charge_highlights: list[RecurringChargeHighlight]
    anomalies: list[AnomalyItem]
    uncategorized_or_new_merchants: list[MonthlyReviewItem]

    def to_dict(self) -> dict[str, object]:
        return {
            "period_start": self.period_start,
            "period_end": self.period_end,
            "account_rollups": [item.to_dict() for item in self.account_rollups],
            "category_totals": [item.to_dict() for item in self.category_totals],
            "changes_vs_prior_month": [item.to_dict() for item in self.changes_vs_prior_month],
            "recurring_charge_highlights": [
                item.to_dict() for item in self.recurring_charge_highlights
            ],
            "anomalies": [item.to_dict() for item in self.anomalies],
            "uncategorized_or_new_merchants": [
                item.to_dict() for item in self.uncategorized_or_new_merchants
            ],
        }
