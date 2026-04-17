from __future__ import annotations

from minx_mcp.core.detectors import (
    detect_training_adherence_drop,
    detect_training_recovery_risk,
    detect_training_volume_stalled,
    detect_training_with_low_protein,
)
from minx_mcp.core.models import (
    DailyTimeline,
    NutritionSnapshot,
    OpenLoopsSnapshot,
    ReadModels,
    SpendingSnapshot,
    TrainingSnapshot,
)


def _build_read_models(
    *,
    training: TrainingSnapshot | None = None,
    nutrition: NutritionSnapshot | None = None,
) -> ReadModels:
    return ReadModels(
        timeline=DailyTimeline(date="2026-04-13", entries=[]),
        spending=SpendingSnapshot(
            date="2026-04-13",
            total_spent_cents=0,
            by_category={},
            top_merchants=[],
            vs_prior_week_pct=None,
            uncategorized_count=0,
            uncategorized_total_cents=0,
        ),
        open_loops=OpenLoopsSnapshot(date="2026-04-13", loops=[]),
        goal_progress=[],
        nutrition=nutrition,
        training=training,
    )


def test_training_detectors_fire_for_expected_signals() -> None:
    training = TrainingSnapshot(
        date="2026-04-13",
        sessions_logged=2,
        total_sets=6,
        total_volume_kg=900.0,
        last_session_at="2026-04-13T08:00:00Z",
        adherence_signal="steady",
    )
    nutrition = NutritionSnapshot(
        date="2026-04-13",
        meal_count=2,
        protein_grams=30.0,
        calories=1500,
        last_meal_at="2026-04-13T12:00:00Z",
        skipped_meal_signals=[],
    )
    read_models = _build_read_models(training=training, nutrition=nutrition)

    stalled = detect_training_volume_stalled(read_models)
    mismatch = detect_training_with_low_protein(read_models)

    assert stalled
    assert stalled[0].insight_type == "training.volume_stalled"
    assert mismatch
    assert mismatch[0].insight_type == "cross.training_nutrition_mismatch"


def test_training_adherence_drop_and_recovery_risk() -> None:
    low_training = TrainingSnapshot(
        date="2026-04-13",
        sessions_logged=1,
        total_sets=3,
        total_volume_kg=300.0,
        last_session_at="2026-04-12T08:00:00Z",
        adherence_signal="low",
    )
    risk_training = TrainingSnapshot(
        date="2026-04-13",
        sessions_logged=6,
        total_sets=20,
        total_volume_kg=3500.0,
        last_session_at="2026-04-13T08:00:00Z",
        adherence_signal="on_track",
    )

    low_models = _build_read_models(training=low_training)
    risk_models = _build_read_models(training=risk_training)

    adherence = detect_training_adherence_drop(low_models)
    recovery = detect_training_recovery_risk(risk_models)

    assert adherence
    assert adherence[0].insight_type == "training.adherence_drop"
    assert recovery
    assert recovery[0].insight_type == "training.recovery_risk"
