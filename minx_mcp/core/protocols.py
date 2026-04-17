from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from minx_mcp.finance.read_models import (
    ImportJobIssue,
    IncomeSummary,
    PeriodComparison,
    SpendingSummary,
    UncategorizedSummary,
)

if TYPE_CHECKING:
    from minx_mcp.core.goal_models import GoalProgress
    from minx_mcp.core.snapshot_models import (
        DailyTimeline,
        InsightCandidate,
        LLMReviewResult,
        NutritionSnapshot,
        OpenLoopsSnapshot,
        SpendingSnapshot,
        TrainingSnapshot,
    )
    from minx_mcp.meals.models import PantryItem


class LLMInterface(Protocol):
    async def evaluate_review(
        self,
        timeline: DailyTimeline,
        spending: SpendingSnapshot,
        open_loops: OpenLoopsSnapshot,
        detector_insights: list[InsightCandidate],
        goal_progress: list[GoalProgress] | None = None,
    ) -> LLMReviewResult: ...


@runtime_checkable
class JSONLLMInterface(Protocol):
    async def run_json_prompt(self, prompt: str) -> str: ...


class FinanceReadInterface(Protocol):
    def get_spending_summary(self, start_date: str, end_date: str) -> SpendingSummary: ...
    def get_uncategorized(self, start_date: str, end_date: str) -> UncategorizedSummary: ...
    def get_import_job_issues(self) -> list[ImportJobIssue]: ...
    def list_account_names(self) -> list[str]: ...
    def get_period_comparison(
        self,
        current_start: str,
        current_end: str,
        prior_start: str,
        prior_end: str,
    ) -> PeriodComparison: ...
    def list_goal_category_names(self) -> list[str]: ...
    def list_spending_merchant_names(self) -> list[str]: ...
    def get_filtered_spending_total(
        self,
        start_date: str,
        end_date: str,
        *,
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
    ) -> int: ...
    def get_filtered_transaction_count(
        self,
        start_date: str,
        end_date: str,
        *,
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
    ) -> int: ...
    def get_income_summary(self, start_date: str, end_date: str) -> IncomeSummary: ...
    def get_net_flow(self, start_date: str, end_date: str) -> int: ...


class MealsReadInterface(Protocol):
    def get_nutrition_summary(self, date: str) -> NutritionSnapshot: ...
    def get_pantry_items(self) -> list[PantryItem]: ...


class TrainingReadInterface(Protocol):
    def get_training_summary(self, date: str) -> TrainingSnapshot: ...


__all__ = [
    "FinanceReadInterface",
    "JSONLLMInterface",
    "LLMInterface",
    "MealsReadInterface",
    "TrainingReadInterface",
]
