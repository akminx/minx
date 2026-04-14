from __future__ import annotations

from minx_mcp.db import get_connection
from minx_mcp.preferences import set_preference
from minx_mcp.training.read_api import TrainingReadAPI
from minx_mcp.training.service import TrainingService


def test_training_read_api_returns_windowed_summary(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        squat = svc.upsert_exercise(display_name="Back Squat", is_compound=True)
        svc.log_session(
            occurred_at="2026-04-11T10:00:00Z",
            sets=[
                {"exercise_id": squat.id, "reps": 5, "weight_kg": 100.0},
                {"exercise_id": squat.id, "reps": 5, "weight_kg": 105.0},
            ],
        )
        svc.log_session(
            occurred_at="2026-04-13T10:00:00Z",
            sets=[
                {"exercise_id": squat.id, "reps": 4, "weight_kg": 120.0},
            ],
        )

    conn = get_connection(db_path)
    try:
        summary = TrainingReadAPI(conn).get_training_summary("2026-04-13")
    finally:
        conn.close()

    assert summary.date == "2026-04-13"
    assert summary.sessions_logged == 2
    assert summary.total_sets == 3
    assert summary.total_volume_kg == 1505.0
    assert summary.last_session_at == "2026-04-13T10:00:00Z"
    assert summary.adherence_signal == "steady"


def test_training_read_api_uses_timezone_local_day_end_boundary(db_path) -> None:
    svc = TrainingService(db_path)
    with svc:
        set_preference(svc.conn, "core", "timezone", "America/Chicago")
        deadlift = svc.upsert_exercise(display_name="Deadlift", is_compound=True)
        # 2026-04-14T04:30Z is 2026-04-13 23:30 in America/Chicago; it should count for 2026-04-13.
        svc.log_session(
            occurred_at="2026-04-14T04:30:00Z",
            sets=[{"exercise_id": deadlift.id, "reps": 5, "weight_kg": 100.0}],
        )

    conn = get_connection(db_path)
    try:
        summary = TrainingReadAPI(conn).get_training_summary("2026-04-13")
    finally:
        conn.close()

    assert summary.sessions_logged == 1
    assert summary.last_session_at == "2026-04-14T04:30:00Z"
