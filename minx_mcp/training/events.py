from __future__ import annotations

from minx_mcp.event_payloads import EventPayload


class WorkoutCompletedPayload(EventPayload):
    session_id: int
    program_id: int | None = None
    set_count: int
    total_volume_kg: float
    occurred_at: str


class TrainingProgramUpdatedPayload(EventPayload):
    program_id: int
    name: str
    is_active: bool
    day_count: int


class TrainingMilestoneReachedPayload(EventPayload):
    milestone_id: int
    milestone_type: str
    summary: str


TRAINING_EVENT_PAYLOADS: dict[str, type[EventPayload]] = {
    "workout.completed": WorkoutCompletedPayload,
    "training.program_updated": TrainingProgramUpdatedPayload,
    "training.milestone_reached": TrainingMilestoneReachedPayload,
}

