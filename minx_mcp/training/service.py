from __future__ import annotations

import json
from datetime import date, timedelta
from sqlite3 import Connection, Row
from typing import Any

from minx_mcp.base_service import BaseService
from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.events import emit_event
from minx_mcp.time_utils import local_day_utc_bounds, resolve_timezone_name, utc_now_isoformat
from minx_mcp.training.models import (
    TrainingExercise,
    TrainingProgram,
    TrainingProgramDay,
    TrainingProgramExercise,
    TrainingProgressSummary,
    TrainingSession,
)
from minx_mcp.training.progression import adherence_signal_for_window

EVENT_SOURCE = "training.service"


class TrainingService(BaseService):
    def upsert_exercise(
        self,
        *,
        display_name: str,
        muscle_group: str | None = None,
        is_compound: bool | None = None,
        notes: str | None = None,
        source: str = "manual",
    ) -> TrainingExercise:
        normalized = _normalize_name(display_name)
        existing = self.conn.execute(
            """
            SELECT id, display_name, normalized_name, muscle_group, is_compound, notes, source
            FROM training_exercises
            WHERE normalized_name = ?
            """,
            (normalized,),
        ).fetchone()

        if existing is None:
            resolved_is_compound = bool(is_compound) if is_compound is not None else False
            cursor = self.conn.execute(
                """
                INSERT INTO training_exercises (
                    display_name, normalized_name, muscle_group, is_compound, notes, source
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    display_name.strip(),
                    normalized,
                    muscle_group,
                    int(resolved_is_compound),
                    notes,
                    source,
                ),
            )
            exercise_id = int(cursor.lastrowid or 0)
        else:
            exercise_id = int(existing["id"])
            resolved_is_compound = (
                bool(int(existing["is_compound"])) if is_compound is None else bool(is_compound)
            )
            self.conn.execute(
                """
                UPDATE training_exercises
                SET display_name = ?,
                    muscle_group = COALESCE(?, muscle_group),
                    is_compound = ?,
                    notes = COALESCE(?, notes),
                    source = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    display_name.strip(),
                    muscle_group,
                    int(resolved_is_compound),
                    notes,
                    source,
                    exercise_id,
                ),
            )

        self.conn.commit()
        return self.get_exercise(exercise_id)

    def get_exercise(self, exercise_id: int) -> TrainingExercise:
        row = self.conn.execute(
            """
            SELECT id, display_name, normalized_name, muscle_group, is_compound, notes, source
            FROM training_exercises
            WHERE id = ?
            """,
            (exercise_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"training exercise {exercise_id} not found")
        return _exercise_from_row(row)

    def list_exercises(self) -> list[TrainingExercise]:
        rows = self.conn.execute(
            """
            SELECT id, display_name, normalized_name, muscle_group, is_compound, notes, source
            FROM training_exercises
            ORDER BY normalized_name ASC, id ASC
            """
        ).fetchall()
        return [_exercise_from_row(row) for row in rows]

    def upsert_program(
        self,
        *,
        name: str,
        description: str | None = None,
        days: list[dict[str, object]] | None = None,
        source: str = "manual",
    ) -> TrainingProgram:
        savepoint = "training_upsert_program"
        self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            normalized_name = _normalize_name(name)
            existing = self.conn.execute(
                """
                SELECT id
                FROM training_programs
                WHERE normalized_name = ?
                """,
                (normalized_name,),
            ).fetchone()
            if existing is None:
                cursor = self.conn.execute(
                    """
                    INSERT INTO training_programs (
                        name, normalized_name, description, is_active, source
                    ) VALUES (?, ?, ?, 0, ?)
                    """,
                    (name.strip(), normalized_name, description, source),
                )
                program_id = int(cursor.lastrowid or 0)
            else:
                program_id = int(existing["id"])
                self.conn.execute(
                    """
                    UPDATE training_programs
                    SET name = ?,
                        description = COALESCE(?, description),
                        source = ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (name.strip(), description, source, program_id),
                )

            self.conn.execute(
                "DELETE FROM training_program_days WHERE program_id = ?", (program_id,)
            )

            normalized_days = _normalize_program_days(days)
            for day, day_index in normalized_days:
                label = _coerce_nullable_string(day.get("label"))
                day_cursor = self.conn.execute(
                    """
                    INSERT INTO training_program_days (program_id, day_index, label)
                    VALUES (?, ?, ?)
                    """,
                    (program_id, day_index, label),
                )
                day_id = int(day_cursor.lastrowid or 0)
                exercise_rows = day.get("exercises")
                if exercise_rows is None:
                    exercise_rows = []
                if not isinstance(exercise_rows, list):
                    raise InvalidInputError("day.exercises must be a list")
                for index, exercise in enumerate(exercise_rows):
                    if not isinstance(exercise, dict):
                        raise InvalidInputError("program exercise rows must be objects")
                    exercise_id = _coerce_optional_int(exercise.get("exercise_id"))
                    if exercise_id is not None:
                        display_name = self.get_exercise(exercise_id).display_name
                    else:
                        display_name = _normalize_name(
                            _coerce_required_string(
                                exercise.get("display_name"), field_name="display_name"
                            )
                        )
                    sort_order = _coerce_non_negative_int(
                        exercise.get("sort_order", index),
                        field_name="sort_order",
                    )
                    self.conn.execute(
                        """
                        INSERT INTO training_program_exercises (
                            program_day_id,
                            exercise_id,
                            display_name,
                            target_sets,
                            target_reps,
                            target_rpe,
                            notes,
                            sort_order
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            day_id,
                            exercise_id,
                            display_name,
                            _coerce_optional_int(exercise.get("target_sets")),
                            _coerce_optional_int(exercise.get("target_reps")),
                            _coerce_optional_float(exercise.get("target_rpe")),
                            _coerce_nullable_string(exercise.get("notes")),
                            sort_order,
                        ),
                    )

            program = self.get_program(program_id)
            _emit_required_event(
                conn=self.conn,
                event_type="training.program_updated",
                occurred_at=utc_now_isoformat(),
                entity_ref=f"program-{program.id}",
                payload={
                    "program_id": program.id,
                    "name": program.name,
                    "is_active": program.is_active,
                    "day_count": len(program.days),
                },
            )
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        self.conn.commit()
        return program

    def get_program(self, program_id: int) -> TrainingProgram:
        program_row = self.conn.execute(
            """
            SELECT id, name, normalized_name, description, is_active, source
            FROM training_programs
            WHERE id = ?
            """,
            (program_id,),
        ).fetchone()
        if program_row is None:
            raise NotFoundError(f"training program {program_id} not found")

        day_rows = self.conn.execute(
            """
            SELECT id, day_index, label
            FROM training_program_days
            WHERE program_id = ?
            ORDER BY day_index ASC, id ASC
            """,
            (program_id,),
        ).fetchall()
        days: list[TrainingProgramDay] = []
        for day_row in day_rows:
            exercise_rows = self.conn.execute(
                """
                SELECT id, exercise_id, display_name, target_sets, target_reps, target_rpe, notes, sort_order
                FROM training_program_exercises
                WHERE program_day_id = ?
                ORDER BY sort_order ASC, id ASC
                """,
                (int(day_row["id"]),),
            ).fetchall()
            days.append(
                TrainingProgramDay(
                    id=int(day_row["id"]),
                    day_index=int(day_row["day_index"]),
                    label=_coerce_nullable_string(day_row["label"]),
                    exercises=[_program_exercise_from_row(row) for row in exercise_rows],
                )
            )

        return TrainingProgram(
            id=int(program_row["id"]),
            name=str(program_row["name"]),
            normalized_name=str(program_row["normalized_name"]),
            description=_coerce_nullable_string(program_row["description"]),
            is_active=bool(int(program_row["is_active"])),
            source=str(program_row["source"]),
            days=days,
        )

    def activate_program(self, program_id: int) -> TrainingProgram:
        savepoint = "training_activate_program"
        self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            self.get_program(program_id)
            self.conn.execute(
                """
                UPDATE training_programs
                SET is_active = 0, updated_at = datetime('now')
                WHERE id != ?
                """,
                (program_id,),
            )
            self.conn.execute(
                """
                UPDATE training_programs
                SET is_active = 1, updated_at = datetime('now')
                WHERE id = ?
                """,
                (program_id,),
            )
            program = self.get_program(program_id)
            _emit_required_event(
                conn=self.conn,
                event_type="training.program_updated",
                occurred_at=utc_now_isoformat(),
                entity_ref=f"program-{program.id}",
                payload={
                    "program_id": program.id,
                    "name": program.name,
                    "is_active": program.is_active,
                    "day_count": len(program.days),
                },
            )
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        self.conn.commit()
        return program

    def log_session(
        self,
        *,
        occurred_at: str,
        sets: list[dict[str, object]],
        program_id: int | None = None,
        notes: str | None = None,
        source: str = "manual",
    ) -> TrainingSession:
        if not sets:
            raise InvalidInputError("sets must include at least one set")
        if program_id is not None:
            self.get_program(program_id)
        savepoint = "training_log_session"
        self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            session_cursor = self.conn.execute(
                """
                INSERT INTO training_sessions (occurred_at, program_id, notes, source)
                VALUES (?, ?, ?, ?)
                """,
                (occurred_at, program_id, notes, source),
            )
            session_id = int(session_cursor.lastrowid or 0)

            total_volume = 0.0
            for index, row in enumerate(sets):
                if not isinstance(row, dict):
                    raise InvalidInputError("sets must contain objects")
                exercise_id = _coerce_optional_int(row.get("exercise_id"))
                if exercise_id is not None:
                    display_name = self.get_exercise(exercise_id).display_name
                else:
                    display_name = _normalize_name(
                        _coerce_required_string(row.get("display_name"), field_name="display_name")
                    )
                reps = _coerce_optional_int(row.get("reps"))
                weight_kg = _coerce_optional_float(row.get("weight_kg"))
                set_index = _coerce_non_negative_int(
                    row.get("set_index", index), field_name="set_index"
                )

                if reps is not None and reps > 0 and weight_kg is not None and weight_kg > 0:
                    total_volume += float(reps) * weight_kg

                self.conn.execute(
                    """
                    INSERT INTO training_session_sets (
                        session_id, exercise_id, display_name, set_index, reps, weight_kg, rpe, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        exercise_id,
                        display_name,
                        set_index,
                        reps,
                        weight_kg,
                        _coerce_optional_float(row.get("rpe")),
                        _coerce_nullable_string(row.get("notes")),
                    ),
                )

            _emit_required_event(
                conn=self.conn,
                event_type="workout.completed",
                occurred_at=occurred_at,
                entity_ref=f"session-{session_id}",
                payload={
                    "session_id": session_id,
                    "program_id": program_id,
                    "set_count": len(sets),
                    "total_volume_kg": total_volume,
                    "occurred_at": occurred_at,
                },
            )

            previous_best = self._max_session_volume(exclude_session_id=session_id)
            if total_volume > 0 and total_volume > previous_best:
                milestone_summary = f"New session volume PR: {total_volume:.1f}kg"
                milestone_cursor = self.conn.execute(
                    """
                    INSERT INTO training_milestones (
                        occurred_at, milestone_type, summary, details_json, source
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        occurred_at,
                        "session_volume_pr",
                        milestone_summary,
                        json.dumps({"session_id": session_id, "total_volume_kg": total_volume}),
                        EVENT_SOURCE,
                    ),
                )
                milestone_id = int(milestone_cursor.lastrowid or 0)
                _emit_required_event(
                    conn=self.conn,
                    event_type="training.milestone_reached",
                    occurred_at=occurred_at,
                    entity_ref=f"milestone-{milestone_id}",
                    payload={
                        "milestone_id": milestone_id,
                        "milestone_type": "session_volume_pr",
                        "summary": milestone_summary,
                    },
                )

            session = self._get_session(session_id)
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        self.conn.commit()
        return session

    def list_sessions(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
    ) -> list[TrainingSession]:
        timezone_name = resolve_timezone_name(self.conn)
        start_utc = (
            local_day_utc_bounds(start_date, timezone_name)[0] if start_date is not None else None
        )
        end_utc = (
            local_day_utc_bounds(end_date, timezone_name)[1] if end_date is not None else None
        )
        where_clauses: list[str] = []
        params: list[object] = []
        if start_utc is not None:
            where_clauses.append("datetime(s.occurred_at) >= datetime(?)")
            params.append(start_utc)
        if end_utc is not None:
            where_clauses.append("datetime(s.occurred_at) < datetime(?)")
            params.append(end_utc)
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        rows = self.conn.execute(
            f"""
            SELECT
                s.id,
                s.occurred_at,
                s.program_id,
                s.notes,
                s.source,
                COUNT(ss.id) AS set_count,
                COALESCE(SUM(COALESCE(ss.reps, 0) * COALESCE(ss.weight_kg, 0)), 0.0) AS total_volume_kg
            FROM training_sessions s
            LEFT JOIN training_session_sets ss ON ss.session_id = s.id
            {where_sql}
            GROUP BY s.id
            ORDER BY s.occurred_at ASC, s.id ASC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        return [_session_from_row(row) for row in rows]

    def get_progress_summary(
        self,
        *,
        as_of: str | None = None,
        lookback_days: int = 7,
    ) -> TrainingProgressSummary:
        if lookback_days <= 0:
            raise InvalidInputError("lookback_days must be positive")
        review_date = as_of or date.today().isoformat()
        timezone_name = resolve_timezone_name(self.conn)
        end_utc = local_day_utc_bounds(review_date, timezone_name)[1]
        start_date = (
            date.fromisoformat(review_date) - timedelta(days=lookback_days - 1)
        ).isoformat()
        start_utc = local_day_utc_bounds(start_date, timezone_name)[0]
        rows = self.conn.execute(
            """
            SELECT
                s.id,
                s.occurred_at,
                COUNT(ss.id) AS set_count,
                COALESCE(SUM(COALESCE(ss.reps, 0) * COALESCE(ss.weight_kg, 0)), 0.0) AS total_volume_kg
            FROM training_sessions s
            LEFT JOIN training_session_sets ss ON ss.session_id = s.id
            WHERE datetime(s.occurred_at) >= datetime(?)
              AND datetime(s.occurred_at) < datetime(?)
            GROUP BY s.id
            ORDER BY s.occurred_at ASC, s.id ASC
            """,
            (start_utc, end_utc),
        ).fetchall()
        filtered = list(rows)
        sessions_logged = len(filtered)
        return TrainingProgressSummary(
            date=review_date,
            sessions_logged=sessions_logged,
            total_sets=sum(int(row["set_count"]) for row in filtered),
            total_volume_kg=sum(float(row["total_volume_kg"]) for row in filtered),
            last_session_at=str(filtered[-1]["occurred_at"]) if filtered else None,
            adherence_signal=adherence_signal_for_window(
                sessions_logged=sessions_logged,
                lookback_days=lookback_days,
            ),
        )

    def _get_session(self, session_id: int) -> TrainingSession:
        row = self.conn.execute(
            """
            SELECT
                s.id,
                s.occurred_at,
                s.program_id,
                s.notes,
                s.source,
                COUNT(ss.id) AS set_count,
                COALESCE(SUM(COALESCE(ss.reps, 0) * COALESCE(ss.weight_kg, 0)), 0.0) AS total_volume_kg
            FROM training_sessions s
            LEFT JOIN training_session_sets ss ON ss.session_id = s.id
            WHERE s.id = ?
            GROUP BY s.id
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"training session {session_id} not found")
        return _session_from_row(row)

    def _max_session_volume(self, *, exclude_session_id: int) -> float:
        row = self.conn.execute(
            """
            SELECT COALESCE(MAX(volume), 0.0) AS max_volume
            FROM (
                SELECT
                    ss.session_id,
                    COALESCE(SUM(COALESCE(ss.reps, 0) * COALESCE(ss.weight_kg, 0)), 0.0) AS volume
                FROM training_session_sets ss
                WHERE ss.session_id != ?
                GROUP BY ss.session_id
            )
            """,
            (exclude_session_id,),
        ).fetchone()
        if row is None:
            return 0.0
        return float(row["max_volume"])


def _exercise_from_row(row: Row) -> TrainingExercise:
    return TrainingExercise(
        id=int(row["id"]),
        display_name=str(row["display_name"]),
        normalized_name=str(row["normalized_name"]),
        muscle_group=_coerce_nullable_string(row["muscle_group"]),
        is_compound=bool(int(row["is_compound"])),
        notes=_coerce_nullable_string(row["notes"]),
        source=str(row["source"]),
    )


def _program_exercise_from_row(row: Row) -> TrainingProgramExercise:
    return TrainingProgramExercise(
        id=int(row["id"]),
        exercise_id=_coerce_optional_int(row["exercise_id"]),
        display_name=str(row["display_name"]),
        target_sets=_coerce_optional_int(row["target_sets"]),
        target_reps=_coerce_optional_int(row["target_reps"]),
        target_rpe=_coerce_optional_float(row["target_rpe"]),
        notes=_coerce_nullable_string(row["notes"]),
        sort_order=int(row["sort_order"]),
    )


def _session_from_row(row: Row) -> TrainingSession:
    return TrainingSession(
        id=int(row["id"]),
        occurred_at=str(row["occurred_at"]),
        program_id=_coerce_optional_int(row["program_id"]),
        notes=_coerce_nullable_string(row["notes"]),
        source=str(row["source"]),
        set_count=int(row["set_count"]),
        total_volume_kg=float(row["total_volume_kg"]),
    )


def _normalize_name(value: str) -> str:
    canonical = " ".join(value.split()).strip().lower()
    if not canonical:
        raise InvalidInputError("name must be non-empty after trimming")
    return canonical


def _coerce_required_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{field_name} must be a non-empty string")
    return value


def _coerce_nullable_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    raise InvalidInputError(f"expected a string value, got {type(value).__name__!r}")


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"expected an integer value, got {value!r}") from exc


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"expected a numeric value, got {value!r}") from exc


def _coerce_positive_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"{field_name} must be an integer greater than zero") from exc
    if parsed <= 0:
        raise InvalidInputError(f"{field_name} must be greater than zero")
    return parsed


def _coerce_non_negative_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError(f"{field_name} must be an integer zero or greater") from exc
    if parsed < 0:
        raise InvalidInputError(f"{field_name} must be zero or greater")
    return parsed


def _normalize_program_days(
    days: list[dict[str, object]] | None,
) -> list[tuple[dict[str, object], int]]:
    normalized_days = days or []
    seen_day_indexes: set[int] = set()
    ordered_days: list[tuple[dict[str, object], int]] = []
    for day in normalized_days:
        if not isinstance(day, dict):
            raise InvalidInputError("days must contain objects")
        day_index = _coerce_positive_int(day.get("day_index"), field_name="day_index")
        if day_index in seen_day_indexes:
            raise InvalidInputError("day_index values must be unique within a program")
        seen_day_indexes.add(day_index)
        ordered_days.append((day, day_index))
    return ordered_days


def _emit_required_event(
    conn: Connection,
    *,
    event_type: str,
    occurred_at: str,
    entity_ref: str | None,
    payload: dict[str, object],
) -> int:
    event_id = emit_event(
        conn,
        event_type=event_type,
        domain="training",
        occurred_at=occurred_at,
        entity_ref=entity_ref,
        source=EVENT_SOURCE,
        payload=payload,
    )
    if event_id is None:
        raise RuntimeError(f"{event_type} event emission failed")
    return int(event_id)
