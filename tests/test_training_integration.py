from __future__ import annotations

import pytest

from minx_mcp.core.models import SnapshotContext
from minx_mcp.core.snapshot import build_daily_snapshot
from minx_mcp.training.service import TrainingService


@pytest.mark.asyncio
async def test_daily_snapshot_includes_training_contribution(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        deadlift = svc.upsert_exercise(display_name="Deadlift", is_compound=True)
        svc.log_session(
            occurred_at="2026-04-13T08:00:00Z",
            sets=[
                {"exercise_id": deadlift.id, "reps": 5, "weight_kg": 140.0},
                {"exercise_id": deadlift.id, "reps": 5, "weight_kg": 145.0},
            ],
        )

    snapshot = await build_daily_snapshot("2026-04-13", SnapshotContext(db_path=db_path))

    assert snapshot.training is not None
    assert snapshot.training.sessions_logged == 1
    assert snapshot.training.total_sets == 2
    assert snapshot.training.total_volume_kg == 1425.0


@pytest.mark.asyncio
async def test_daily_snapshot_emits_cross_training_nutrition_mismatch(db_path) -> None:
    from minx_mcp.meals.service import MealsService

    training = TrainingService(db_path)
    with training:
        deadlift = training.upsert_exercise(display_name="Deadlift", is_compound=True)
        training.log_session(
            occurred_at="2026-04-12T08:00:00Z",
            sets=[
                {"exercise_id": deadlift.id, "reps": 5, "weight_kg": 140.0},
            ],
        )
        training.log_session(
            occurred_at="2026-04-13T08:00:00Z",
            sets=[
                {"exercise_id": deadlift.id, "reps": 5, "weight_kg": 145.0},
            ],
        )
    meals = MealsService(db_path)
    with meals:
        meals.log_meal(
            occurred_at="2026-04-13T12:00:00Z",
            meal_kind="lunch",
            protein_grams=20.0,
            calories=900,
        )

    snapshot = await build_daily_snapshot("2026-04-13", SnapshotContext(db_path=db_path))

    types = {signal.insight_type for signal in snapshot.signals}
    assert "cross.training_nutrition_mismatch" in types
