from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FinanceQueryIntent = Literal["list_transactions", "sum_spending", "count_transactions"]
FinanceQueryClarificationType = Literal[
    "ambiguous_merchant",
    "unknown_category",
    "unknown_merchant",
    "unknown_account",
    "missing_date_range",
]


@dataclass(frozen=True)
class FinanceQueryFilters:
    start_date: str | None = None
    end_date: str | None = None
    category_name: str | None = None
    merchant: str | None = None
    account_name: str | None = None
    description_contains: str | None = None

    def to_public_dict(self) -> dict[str, str]:
        values = {
            "start_date": self.start_date,
            "end_date": self.end_date,
            "category_name": self.category_name,
            "merchant": self.merchant,
            "account_name": self.account_name,
            "description_contains": self.description_contains,
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True)
class FinanceQueryPlan:
    intent: FinanceQueryIntent
    filters: FinanceQueryFilters
    confidence: float
    needs_clarification: bool = False
    clarification_type: FinanceQueryClarificationType | None = None
    clarification_template: str | None = None
    clarification_slots: dict[str, object] | None = None
    question: str | None = None
    options: list[str] | None = None

    def __post_init__(self) -> None:
        if self.needs_clarification:
            if self.clarification_type is None:
                raise ValueError("clarification_type is required when clarification is needed")
            if self.clarification_template is None:
                raise ValueError("clarification_template is required when clarification is needed")
            if self.clarification_slots is None:
                raise ValueError("clarification_slots is required when clarification is needed")
            if self.question is None:
                raise ValueError("question is required when clarification is needed")
        elif any(
            value is not None
            for value in (
                self.clarification_type,
                self.clarification_template,
                self.clarification_slots,
                self.question,
                self.options,
            )
        ):
            raise ValueError(
                "clarification fields must be omitted when clarification is not needed"
            )


__all__ = [
    "FinanceQueryClarificationType",
    "FinanceQueryFilters",
    "FinanceQueryIntent",
    "FinanceQueryPlan",
]
