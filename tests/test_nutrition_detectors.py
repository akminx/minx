from __future__ import annotations

from minx_mcp.core.detectors import DETECTORS, detect_low_protein, detect_skipped_meals
from minx_mcp.core.models import (
    DailyTimeline,
    NutritionSnapshot,
    OpenLoopsSnapshot,
    ReadModels,
    SpendingSnapshot,
)


def _build_read_models(nutrition: NutritionSnapshot | None) -> ReadModels:
    return ReadModels(
        timeline=DailyTimeline(date="2026-04-12", entries=[]),
        spending=SpendingSnapshot(
            date="2026-04-12",
            total_spent_cents=0,
            by_category={},
            top_merchants=[],
            vs_prior_week_pct=None,
            uncategorized_count=0,
            uncategorized_total_cents=0,
        ),
        open_loops=OpenLoopsSnapshot(date="2026-04-12", loops=[]),
        goal_progress=[],
        nutrition=nutrition,
    )


def test_detector_registry_includes_nutrition_detectors() -> None:
    assert "nutrition.low_protein" in [detector.key for detector in DETECTORS]
    assert "nutrition.skipped_meals" in [detector.key for detector in DETECTORS]


def test_nutrition_detectors_fire_for_low_protein_and_skipped_meals() -> None:
    nutrition = NutritionSnapshot(
        date="2026-04-12",
        meal_count=1,
        protein_grams=25.0,
        calories=600,
        last_meal_at="2026-04-12T19:00:00Z",
        skipped_meal_signals=["no breakfast logged", "no lunch logged"],
    )
    read_models = _build_read_models(nutrition)

    assert detect_low_protein(read_models).insights[0].insight_type == "nutrition.low_protein"
    assert detect_skipped_meals(read_models).insights[0].insight_type == "nutrition.skipped_meals"
