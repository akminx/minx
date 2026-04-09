from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GoalCaptureInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["create", "update", "clarify", "no_match"]
    confidence: float
    subject_kind: Literal["category", "merchant"] | None = None
    subject: str | None = None
    period: Literal["daily", "weekly", "monthly"] | None = None
    target_value: int | None = None


class FinanceQueryFiltersInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: str | None = None
    end_date: str | None = None
    category_name: str | None = None
    merchant: str | None = None
    account_name: str | None = None
    description_contains: str | None = None


class FinanceQueryInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["list_transactions", "sum_spending", "count_transactions"]
    filters: FinanceQueryFiltersInterpretation = Field(
        default_factory=FinanceQueryFiltersInterpretation
    )
    confidence: float
    needs_clarification: bool = False
    clarification_type: Literal[
        "ambiguous_merchant",
        "unknown_category",
        "unknown_merchant",
        "unknown_account",
        "missing_date_range",
    ] | None = None
    question: str | None = None
    options: list[str] | None = None
