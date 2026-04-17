from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TrainingExercise:
    id: int
    display_name: str
    normalized_name: str
    muscle_group: str | None
    is_compound: bool
    notes: str | None
    source: str


@dataclass(frozen=True)
class TrainingProgramExercise:
    id: int
    exercise_id: int | None
    display_name: str
    target_sets: int | None
    target_reps: int | None
    target_rpe: float | None
    notes: str | None
    sort_order: int


@dataclass(frozen=True)
class TrainingProgramDay:
    id: int
    day_index: int
    label: str | None
    exercises: list[TrainingProgramExercise] = field(default_factory=list)


@dataclass(frozen=True)
class TrainingProgram:
    id: int
    name: str
    normalized_name: str
    description: str | None
    is_active: bool
    source: str
    days: list[TrainingProgramDay] = field(default_factory=list)


@dataclass(frozen=True)
class TrainingSession:
    id: int
    occurred_at: str
    program_id: int | None
    notes: str | None
    source: str
    set_count: int
    total_volume_kg: float


@dataclass(frozen=True)
class TrainingProgressSummary:
    date: str
    sessions_logged: int
    total_sets: int
    total_volume_kg: float
    last_session_at: str | None
    adherence_signal: str
