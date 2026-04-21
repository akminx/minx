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
from minx_mcp.core.memory_detectors import (
    detect_category_preference,
    detect_recurring_merchant_pattern,
    detect_schedule_pattern,
)
from minx_mcp.core.memory_models import DetectorResult
from minx_mcp.core.models import InsightCandidate, OpenLoop, ReadModels
from minx_mcp.money import format_cents

DetectorFn = Callable[[ReadModels], DetectorResult]
LOW_PROTEIN_THRESHOLD_GRAMS = 50.0


@dataclass(frozen=True)
class Detector:
    key: str
    fn: DetectorFn
    enabled_by_default: bool = True
    tags: frozenset[str] = field(default_factory=frozenset)


def detect_spending_spike(read_models: ReadModels) -> DetectorResult:
    spending = read_models.spending
    change_pct = spending.vs_prior_week_pct
    if change_pct is None or change_pct < 25:
        return DetectorResult.empty()

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
                f"Top spending category today: {primary_category} ({format_cents(primary_total)})."
            )

    severity = "alert" if change_pct >= 50 else "warning"
    summary = (
        f"Spending is up {change_pct:.1f}% versus last week."
        if primary_category is None
        else f"Spending is up {change_pct:.1f}% versus last week, led by {primary_category}."
    )
    dedupe_bucket = slugify(primary_category or "overall")
    return DetectorResult.insights_only(
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
    )


def detect_open_loops(read_models: ReadModels) -> DetectorResult:
    insights = [
        InsightCandidate(
            insight_type="finance.open_loop",
            dedupe_key=_open_loop_dedupe_key(read_models.open_loops.date, loop),
            summary=loop.description,
            supporting_signals=[loop.description],
            confidence=0.9 if loop.loop_type in _ACTION_NEEDED_LOOP_TYPES else 0.8,
            severity=loop.severity,
            actionability=(
                "action_needed" if loop.loop_type in _ACTION_NEEDED_LOOP_TYPES else "suggestion"
            ),
            source="detector",
        )
        for loop in read_models.open_loops.loops
    ]
    return DetectorResult(tuple(insights), ())


def detect_low_protein(read_models: ReadModels) -> DetectorResult:
    nutrition = read_models.nutrition
    if nutrition is None or nutrition.protein_grams is None:
        return DetectorResult.empty()
    if nutrition.protein_grams >= LOW_PROTEIN_THRESHOLD_GRAMS:
        return DetectorResult.empty()
    return DetectorResult.insights_only(
        InsightCandidate(
            insight_type="nutrition.low_protein",
            dedupe_key=f"{nutrition.date}:low_protein",
            summary=(
                f"Protein intake is {nutrition.protein_grams:.0f}g today, "
                f"below {LOW_PROTEIN_THRESHOLD_GRAMS:.0f}g target."
            ),
            supporting_signals=[f"{nutrition.meal_count} meals logged"],
            confidence=0.9,
            severity="info",
            actionability="suggestion",
            source="detector",
        )
    )


def detect_skipped_meals(read_models: ReadModels) -> DetectorResult:
    nutrition = read_models.nutrition
    if nutrition is None or not nutrition.skipped_meal_signals:
        return DetectorResult.empty()
    skipped = ", ".join(
        signal.replace("no ", "").replace(" logged", "")
        for signal in nutrition.skipped_meal_signals
    )
    return DetectorResult.insights_only(
        InsightCandidate(
            insight_type="nutrition.skipped_meals",
            dedupe_key=f"{nutrition.date}:skipped_meals",
            summary=f"Missing meals today: {skipped}.",
            supporting_signals=nutrition.skipped_meal_signals,
            confidence=0.85,
            severity="info",
            actionability="suggestion",
            source="detector",
        )
    )


def detect_training_adherence_drop(read_models: ReadModels) -> DetectorResult:
    training = read_models.training
    if training is None or training.adherence_signal != "low":
        return DetectorResult.empty()
    return DetectorResult.insights_only(
        InsightCandidate(
            insight_type="training.adherence_drop",
            dedupe_key=f"{training.date}:training_adherence_drop",
            summary=f"Training adherence dropped this week ({training.sessions_logged} session logged).",
            supporting_signals=[
                f"adherence signal: {training.adherence_signal}",
                f"sessions in last window: {training.sessions_logged}",
            ],
            confidence=0.88,
            severity="warning",
            actionability="action_needed",
            source="detector",
        )
    )


def detect_training_volume_stalled(read_models: ReadModels) -> DetectorResult:
    training = read_models.training
    if training is None:
        return DetectorResult.empty()
    if training.sessions_logged < 2:
        return DetectorResult.empty()
    if training.total_volume_kg >= 1_000.0:
        return DetectorResult.empty()
    return DetectorResult.insights_only(
        InsightCandidate(
            insight_type="training.volume_stalled",
            dedupe_key=f"{training.date}:training_volume_stalled",
            summary="Training volume looks stalled over the recent window.",
            supporting_signals=[
                f"total volume: {training.total_volume_kg:.1f}kg",
                f"sessions logged: {training.sessions_logged}",
            ],
            confidence=0.8,
            severity="info",
            actionability="suggestion",
            source="detector",
        )
    )


def detect_training_recovery_risk(read_models: ReadModels) -> DetectorResult:
    training = read_models.training
    if training is None:
        return DetectorResult.empty()
    if training.sessions_logged < 5:
        return DetectorResult.empty()
    return DetectorResult.insights_only(
        InsightCandidate(
            insight_type="training.recovery_risk",
            dedupe_key=f"{training.date}:training_recovery_risk",
            summary="Training frequency may be pushing recovery limits.",
            supporting_signals=[
                f"sessions logged: {training.sessions_logged}",
                f"last session: {training.last_session_at or 'unknown'}",
            ],
            confidence=0.78,
            severity="warning",
            actionability="suggestion",
            source="detector",
        )
    )


def detect_training_with_low_protein(read_models: ReadModels) -> DetectorResult:
    training = read_models.training
    nutrition = read_models.nutrition
    if training is None or nutrition is None or nutrition.protein_grams is None:
        return DetectorResult.empty()
    if training.adherence_signal not in {"steady", "on_track"}:
        return DetectorResult.empty()
    if training.sessions_logged < 2:
        return DetectorResult.empty()
    if nutrition.protein_grams >= LOW_PROTEIN_THRESHOLD_GRAMS:
        return DetectorResult.empty()
    return DetectorResult.insights_only(
        InsightCandidate(
            insight_type="cross.training_nutrition_mismatch",
            dedupe_key=f"{training.date}:training_nutrition_mismatch",
            summary="Training adherence is steady, but protein intake is still low.",
            supporting_signals=[
                f"adherence signal: {training.adherence_signal}",
                f"protein intake: {nutrition.protein_grams:.0f}g",
            ],
            confidence=0.84,
            severity="info",
            actionability="suggestion",
            source="detector",
        )
    )


DETECTORS: list[Detector] = [
    Detector(key="finance.spending_spike", fn=detect_spending_spike, tags=frozenset({"finance"})),
    Detector(key="finance.open_loop", fn=detect_open_loops, tags=frozenset({"finance"})),
    Detector(key="nutrition.low_protein", fn=detect_low_protein, tags=frozenset({"nutrition"})),
    Detector(key="nutrition.skipped_meals", fn=detect_skipped_meals, tags=frozenset({"nutrition"})),
    Detector(
        key="training.adherence_drop",
        fn=detect_training_adherence_drop,
        tags=frozenset({"training"}),
    ),
    Detector(
        key="training.volume_stalled",
        fn=detect_training_volume_stalled,
        tags=frozenset({"training"}),
    ),
    Detector(
        key="training.recovery_risk", fn=detect_training_recovery_risk, tags=frozenset({"training"})
    ),
    Detector(
        key="cross.training_nutrition_mismatch",
        fn=detect_training_with_low_protein,
        tags=frozenset({"training", "nutrition"}),
    ),
    Detector(key="core.goal_drift", fn=detect_goal_drift, tags=frozenset({"goals"})),
    Detector(
        key="finance.category_drift", fn=detect_category_drift, tags=frozenset({"finance", "goals"})
    ),
    Detector(
        key="finance.goal_risk", fn=detect_goal_finance_risks, tags=frozenset({"finance", "goals"})
    ),
    Detector(
        key="memory.recurring_merchant",
        fn=detect_recurring_merchant_pattern,
        tags=frozenset({"finance", "memory"}),
    ),
    Detector(
        key="memory.category_preference",
        fn=detect_category_preference,
        tags=frozenset({"finance", "memory"}),
    ),
    Detector(
        key="memory.schedule_pattern",
        fn=detect_schedule_pattern,
        tags=frozenset({"meals", "memory"}),
    ),
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
