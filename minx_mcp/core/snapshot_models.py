from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from minx_mcp.core.goal_models import GoalProgress
from minx_mcp.core.protocols import (
    FinanceReadInterface,
    MealsReadInterface,
    TrainingReadInterface,
)


@dataclass(frozen=True)
class TimelineEntry:
    occurred_at: str
    domain: str
    event_type: str
    summary: str
    entity_ref: str | None


@dataclass(frozen=True)
class DailyTimeline:
    date: str
    entries: list[TimelineEntry]


@dataclass(frozen=True)
class SpendingSnapshot:
    date: str
    total_spent_cents: int
    by_category: dict[str, int]
    top_merchants: list[tuple[str, int]]
    vs_prior_week_pct: float | None
    uncategorized_count: int
    uncategorized_total_cents: int


@dataclass(frozen=True)
class OpenLoop:
    domain: str
    loop_type: str
    description: str
    count: int | None
    severity: str


@dataclass(frozen=True)
class OpenLoopsSnapshot:
    date: str
    loops: list[OpenLoop]


@dataclass(frozen=True)
class NutritionSnapshot:
    date: str
    meal_count: int
    protein_grams: float | None
    calories: int | None
    last_meal_at: str | None
    skipped_meal_signals: list[str]


@dataclass(frozen=True)
class TrainingSnapshot:
    date: str
    sessions_logged: int
    total_sets: int
    total_volume_kg: float
    last_session_at: str | None
    adherence_signal: str


@dataclass(frozen=True)
class ReadModels:
    timeline: DailyTimeline
    spending: SpendingSnapshot
    open_loops: OpenLoopsSnapshot
    goal_progress: list[GoalProgress]
    nutrition: NutritionSnapshot | None = None
    training: TrainingSnapshot | None = None
    finance_api: FinanceReadInterface | None = None
    meals_api: MealsReadInterface | None = None
    training_api: TrainingReadInterface | None = None


@dataclass(frozen=True)
class InsightCandidate:
    insight_type: str
    dedupe_key: str
    summary: str
    supporting_signals: list[str]
    confidence: float
    severity: str
    actionability: str
    source: str


@dataclass(frozen=True)
class LLMReviewResult:
    additional_insights: list[InsightCandidate]
    narrative: str
    next_day_focus: list[str]


@dataclass(frozen=True)
class PersistenceWarning:
    sink: str
    message: str


@dataclass(frozen=True)
class DailySnapshot:
    date: str
    timeline: DailyTimeline
    spending: SpendingSnapshot
    open_loops: OpenLoopsSnapshot
    goal_progress: list[GoalProgress]
    signals: list[InsightCandidate]
    attention_items: list[str]
    nutrition: NutritionSnapshot | None = None
    training: TrainingSnapshot | None = None
    persistence_warning: PersistenceWarning | None = None


@dataclass(frozen=True)
class DurabilitySinkFailure:
    """One failed durability step in the daily review pipeline."""

    sink: str
    error: Exception


@dataclass(frozen=True)
class SnapshotContext:
    db_path: str | Path
    finance_api: FinanceReadInterface | None = None
    meals_api: MealsReadInterface | None = None
    training_api: TrainingReadInterface | None = None


__all__ = [
    "DailySnapshot",
    "DailyTimeline",
    "DurabilitySinkFailure",
    "InsightCandidate",
    "LLMReviewResult",
    "NutritionSnapshot",
    "OpenLoop",
    "OpenLoopsSnapshot",
    "PersistenceWarning",
    "ReadModels",
    "SnapshotContext",
    "SpendingSnapshot",
    "TimelineEntry",
    "TrainingSnapshot",
]
