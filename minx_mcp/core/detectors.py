from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from minx_mcp.core._utils import slugify
from minx_mcp.core.goal_detectors import (
    detect_category_drift,
    detect_goal_drift,
    detect_goal_finance_risks,
)
from minx_mcp.core.models import InsightCandidate, OpenLoop, ReadModels
from minx_mcp.money import format_cents

DetectorFn = Callable[[ReadModels], list[InsightCandidate]]


@dataclass(frozen=True)
class Detector:
    key: str
    fn: DetectorFn
    enabled_by_default: bool = True
    tags: frozenset[str] = field(default_factory=frozenset)


def detect_spending_spike(read_models: ReadModels) -> list[InsightCandidate]:
    spending = read_models.spending
    change_pct = spending.vs_prior_week_pct
    if change_pct is None or change_pct < 25:
        return []

    primary_category, primary_total = _primary_category(spending.by_category)
    supporting_signals = [
        f"Spending increased {change_pct:.1f}% versus the prior week.",
    ]
    if primary_category is not None:
        primary_share = _percentage(primary_total, spending.total_spent_cents)
        if primary_share > 60:
            supporting_signals.append(
                f"{primary_category} drove {primary_share:.0f}% of today's spending."
            )
        else:
            supporting_signals.append(
                f"Top spending category today: {primary_category} "
                f"({format_cents(primary_total)})."
            )

    severity = "alert" if change_pct >= 50 else "warning"
    summary = (
        f"Spending is up {change_pct:.1f}% versus last week."
        if primary_category is None
        else f"Spending is up {change_pct:.1f}% versus last week, led by {primary_category}."
    )
    dedupe_bucket = slugify(primary_category or "overall")
    return [
        InsightCandidate(
            insight_type="finance.spending_spike",
            dedupe_key=f"{spending.date}:spending_spike:{dedupe_bucket}",
            summary=summary,
            supporting_signals=supporting_signals,
            confidence=0.85 if severity == "warning" else 0.92,
            severity=severity,
            actionability="suggestion",
            source="detector",
        )
    ]


def detect_open_loops(read_models: ReadModels) -> list[InsightCandidate]:
    insights: list[InsightCandidate] = []
    for loop in read_models.open_loops.loops:
        insights.append(
            InsightCandidate(
                insight_type="finance.open_loop",
                dedupe_key=_open_loop_dedupe_key(read_models.open_loops.date, loop),
                summary=loop.description,
                supporting_signals=[loop.description],
                confidence=0.9 if loop.loop_type in _ACTION_NEEDED_LOOP_TYPES else 0.8,
                severity=loop.severity,
                actionability=(
                    "action_needed"
                    if loop.loop_type in _ACTION_NEEDED_LOOP_TYPES
                    else "suggestion"
                ),
                source="detector",
            )
        )
    return insights


DETECTORS: list[Detector] = [
    Detector(key="finance.spending_spike", fn=detect_spending_spike, tags=frozenset({"finance"})),
    Detector(key="finance.open_loops", fn=detect_open_loops, tags=frozenset({"finance"})),
    Detector(key="core.goal_drift", fn=detect_goal_drift, tags=frozenset({"goals"})),
    Detector(key="finance.category_drift", fn=detect_category_drift, tags=frozenset({"finance", "goals"})),
    Detector(key="finance.goal_risk", fn=detect_goal_finance_risks, tags=frozenset({"finance", "goals"})),
]


_ACTION_NEEDED_LOOP_TYPES = {"failed_import_job", "stale_import_job"}


def _primary_category(by_category: dict[str, int]) -> tuple[str | None, int]:
    if not by_category:
        return None, 0
    return max(
        by_category.items(),
        key=lambda item: (item[1], item[0]),
    )


def _percentage(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return (part / total) * 100


def _open_loop_dedupe_key(review_date: str, loop: OpenLoop) -> str:
    if loop.loop_type == "uncategorized_transactions":
        identity = "uncategorized"
    else:
        identity = _extract_job_identity(loop.description)
    return f"{review_date}:open_loop:{loop.loop_type}:{identity}"


def _extract_job_identity(description: str) -> str:
    match = re.search(r"\bjob\s+([A-Za-z0-9._:-]+)\b", description)
    if match:
        return f"import-job-{slugify(match.group(1))}"
    return f"loop-{slugify(description)}"
