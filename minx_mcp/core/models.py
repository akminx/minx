from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from minx_mcp.finance.read_api import FinanceReadAPI


class LLMInterface(Protocol):
    async def evaluate_review(
        self,
        timeline: DailyTimeline,
        spending: SpendingSnapshot,
        open_loops: OpenLoopsSnapshot,
        detector_insights: list[InsightCandidate],
    ) -> "LLMReviewResult": ...


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
class ReadModels:
    timeline: DailyTimeline
    spending: SpendingSnapshot
    open_loops: OpenLoopsSnapshot


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
class DailyReview:
    date: str
    timeline: DailyTimeline
    spending: SpendingSnapshot
    open_loops: OpenLoopsSnapshot
    insights: list[InsightCandidate]
    narrative: str
    next_day_focus: list[str]
    llm_enriched: bool


@dataclass(frozen=True)
class ReviewContext:
    db_path: str | Path
    finance_api: "FinanceReadAPI"
    vault_writer: object
    llm: LLMInterface | None = None
