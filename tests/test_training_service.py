from __future__ import annotations

import pytest

import minx_mcp.training.service as training_service_module
from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.events import query_events
from minx_mcp.db import get_connection
from minx_mcp.training.service import TrainingService


def test_upsert_exercise_is_idempotent_by_normalized_name(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        first = svc.upsert_exercise(
            display_name="Bench Press",
            muscle_group="chest",
            is_compound=True,
        )
        second = svc.upsert_exercise(
            display_name=" bench press ",
            notes="barbell bench",
        )
        exercises = svc.list_exercises()

    assert first.id == second.id
    assert second.normalized_name == "bench press"
    assert second.notes == "barbell bench"
    assert second.is_compound is True
    assert len(exercises) == 1
    assert exercises[0].display_name == "bench press"


def test_upsert_program_replaces_day_exercises_and_emits_event(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        bench = svc.upsert_exercise(display_name="Bench Press")
        first = svc.upsert_program(
            name="Upper Split",
            days=[
                {
                    "day_index": 1,
                    "label": "Push",
                    "exercises": [
                        {"exercise_id": bench.id, "target_sets": 3, "target_reps": 5},
                    ],
                }
            ],
        )
        second = svc.upsert_program(
            name="Upper Split",
            days=[
                {
                    "day_index": 1,
                    "label": "Push Day",
                    "exercises": [
                        {"exercise_id": bench.id, "target_sets": 5, "target_reps": 5},
                    ],
                }
            ],
        )
        detail = svc.get_program(second.id)

    assert first.id == second.id
    assert len(detail.days) == 1
    assert detail.days[0].label == "Push Day"
    assert detail.days[0].exercises[0].target_sets == 5

    conn = get_connection(db_path)
    try:
        events = query_events(
            conn,
            domain="training",
            event_type="training.program_updated",
        )
    finally:
        conn.close()
    assert len(events) == 2


def test_activate_program_marks_single_active_row(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        first = svc.upsert_program(name="Program A", days=[])
        second = svc.upsert_program(name="Program B", days=[])
        svc.activate_program(first.id)
        activated = svc.activate_program(second.id)
        rows = svc.conn.execute(
            "SELECT id, is_active FROM training_programs ORDER BY id ASC"
        ).fetchall()

    assert activated.id == second.id
    assert [(row["id"], row["is_active"]) for row in rows] == [
        (first.id, 0),
        (second.id, 1),
    ]


def test_log_session_persists_sets_emits_events_and_updates_progress(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        squat = svc.upsert_exercise(display_name="Back Squat", is_compound=True)
        svc.log_session(
            occurred_at="2026-04-10T12:00:00Z",
            sets=[
                {"exercise_id": squat.id, "reps": 5, "weight_kg": 100.0, "set_index": 0},
                {"exercise_id": squat.id, "reps": 5, "weight_kg": 105.0, "set_index": 1},
            ],
        )
        svc.log_session(
            occurred_at="2026-04-12T12:00:00Z",
            sets=[
                {"exercise_id": squat.id, "reps": 6, "weight_kg": 110.0, "set_index": 0},
            ],
        )
        sessions = svc.list_sessions(start_date="2026-04-01", end_date="2026-04-13")
        summary = svc.get_progress_summary(as_of="2026-04-13", lookback_days=7)

    assert len(sessions) == 2
    assert sessions[0].set_count == 2
    assert sessions[1].set_count == 1
    assert summary.sessions_logged == 2
    assert summary.total_sets == 3
    assert summary.total_volume_kg == 1685.0
    assert summary.adherence_signal == "steady"

    conn = get_connection(db_path)
    try:
        workout_events = query_events(
            conn,
            domain="training",
            event_type="workout.completed",
        )
        milestone_events = query_events(
            conn,
            domain="training",
            event_type="training.milestone_reached",
        )
    finally:
        conn.close()
    assert len(workout_events) == 2
    assert workout_events[0].payload["set_count"] == 2
    assert len(milestone_events) >= 1


def test_activate_program_missing_id_does_not_clear_existing_active_program(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        first = svc.upsert_program(name="Program A", days=[])
        second = svc.upsert_program(name="Program B", days=[])
        svc.activate_program(first.id)
        with pytest.raises(NotFoundError):
            svc.activate_program(999999)
        rows = svc.conn.execute(
            "SELECT id, is_active FROM training_programs ORDER BY id ASC"
        ).fetchall()

    assert [(row["id"], row["is_active"]) for row in rows] == [
        (first.id, 1),
        (second.id, 0),
    ]


def test_upsert_program_invalid_payload_leaves_existing_program_days_intact(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        bench = svc.upsert_exercise(display_name="Bench Press")
        program = svc.upsert_program(
            name="Upper Split",
            days=[
                {
                    "day_index": 1,
                    "label": "Push",
                    "exercises": [{"exercise_id": bench.id, "target_sets": 3, "target_reps": 5}],
                }
            ],
        )
        with pytest.raises(InvalidInputError):
            svc.upsert_program(
                name="Upper Split",
                days=[
                    {
                        "day_index": 1,
                        "label": "Push",
                        "exercises": "not-a-list",  # type: ignore[arg-type]
                    }
                ],
            )
        detail = svc.get_program(program.id)

    assert len(detail.days) == 1
    assert detail.days[0].label == "Push"
    assert len(detail.days[0].exercises) == 1


def test_log_session_invalid_set_row_does_not_persist_partial_session(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        squat = svc.upsert_exercise(display_name="Back Squat")
        with pytest.raises(InvalidInputError):
            svc.log_session(
                occurred_at="2026-04-14T08:00:00Z",
                sets=[
                    {"exercise_id": squat.id, "reps": 5, "weight_kg": 100.0},
                    {"display_name": "   ", "reps": 5, "weight_kg": 105.0},
                ],
            )
        session_count = svc.conn.execute("SELECT COUNT(*) FROM training_sessions").fetchone()[0]
        set_count = svc.conn.execute("SELECT COUNT(*) FROM training_session_sets").fetchone()[0]

    assert session_count == 0
    assert set_count == 0


def test_upsert_program_rejects_duplicate_day_index_as_invalid_input(db_path) -> None:
    svc = TrainingService(db_path)
    with pytest.raises(InvalidInputError), svc:
        svc.upsert_program(
            name="Duplicate Days",
            days=[
                {"day_index": 1, "label": "A", "exercises": []},
                {"day_index": 1, "label": "B", "exercises": []},
            ],
        )


def test_upsert_exercise_preserves_display_name_casing(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        exercise = svc.upsert_exercise(display_name="Bench Press")
    assert exercise.display_name == "Bench Press"
    # not "bench press"


def test_upsert_exercise_update_preserves_display_name_casing(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        svc.upsert_exercise(display_name="bench press")
        updated = svc.upsert_exercise(display_name="Bench Press")
    assert updated.display_name == "Bench Press"


def test_coerce_optional_int_raises_invalid_input_on_bad_value(db_path) -> None:
    from minx_mcp.training.service import _coerce_optional_int

    with pytest.raises(InvalidInputError):
        _coerce_optional_int("not_a_number")


def test_coerce_optional_float_raises_invalid_input_on_bad_value(db_path) -> None:
    from minx_mcp.training.service import _coerce_optional_float

    with pytest.raises(InvalidInputError):
        _coerce_optional_float("not_a_number")


def test_coerce_nullable_string_raises_invalid_input_on_non_string(db_path) -> None:
    from minx_mcp.training.service import _coerce_nullable_string

    with pytest.raises(InvalidInputError):
        _coerce_nullable_string(42)


def test_upsert_program_rolls_back_on_event_emission_failure(db_path, monkeypatch) -> None:
    svc = TrainingService(db_path)
    monkeypatch.setattr(training_service_module, "emit_event", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError), svc:
        svc.upsert_program(
            name="Event Failure Program",
            days=[{"day_index": 1, "label": "A", "exercises": []}],
        )
    conn = get_connection(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM training_programs").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_log_session_rolls_back_on_event_emission_failure(db_path, monkeypatch) -> None:
    svc = TrainingService(db_path)
    monkeypatch.setattr(training_service_module, "emit_event", lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError), svc:
        squat = svc.upsert_exercise(display_name="Back Squat")
        svc.log_session(
            occurred_at="2026-04-14T08:00:00Z",
            sets=[{"exercise_id": squat.id, "reps": 5, "weight_kg": 100.0}],
        )
    conn = get_connection(db_path)
    try:
        session_count = conn.execute("SELECT COUNT(*) FROM training_sessions").fetchone()[0]
        set_count = conn.execute("SELECT COUNT(*) FROM training_session_sets").fetchone()[0]
    finally:
        conn.close()
    assert session_count == 0
    assert set_count == 0
