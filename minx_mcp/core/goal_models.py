from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class GoalCreateInput:
    goal_type: str
    title: str
    metric_type: str
    target_value: int
    period: str
    domain: str
    category_names: list[str]
    merchant_names: list[str]
    account_names: list[str]
    starts_on: str
    ends_on: str | None
    notes: str | None


@dataclass(frozen=True)
class GoalUpdateInput:
    title: str | None = None
    target_value: int | None = None
    status: str | None = None
    ends_on: str | None = None
    notes: str | None = None
    clear_ends_on: bool = False
    clear_notes: bool = False


@dataclass(frozen=True)
class GoalRecord:
    id: int
    goal_type: str
    title: str
    status: str
    metric_type: str
    target_value: int
    period: str
    domain: str
    category_names: list[str]
    merchant_names: list[str]
    account_names: list[str]
    starts_on: str
    ends_on: str | None
    notes: str | None
    created_at: str
    updated_at: str


GoalCaptureResultType = Literal["create", "update", "clarify", "no_match"]
GoalCaptureAction = Literal["goal_create", "goal_update"]
GoalCaptureClarificationType = Literal[
    "ambiguous_goal",
    "ambiguous_subject",
    "missing_goal",
    "missing_target",
    "vague_intent",
]
GoalCaptureOptionKind = Literal["category", "merchant", "goal"]


@dataclass(frozen=True)
class GoalCaptureOption:
    goal_id: int | None = None
    title: str | None = None
    period: str | None = None
    target_value: int | None = None
    status: str | None = None
    filter_summary: str | None = None
    kind: GoalCaptureOptionKind | None = None
    label: str | None = None
    category_name: str | None = None
    merchant_name: str | None = None
    payload_fragment: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.kind is None:
            raise ValueError("kind is required for goal capture options")
        if self.kind not in {"category", "merchant", "goal"}:
            raise ValueError("kind must be one of 'category', 'merchant', or 'goal'")
        if self.kind == "category":
            if self.category_name is None:
                raise ValueError("category_name is required for category options")
            if self.label is None:
                raise ValueError("label is required for category options")
            if self.payload_fragment is None:
                raise ValueError("payload_fragment is required for category options")
            self._validate_variant_alignment(
                variant_label="category",
                expected_label=self.category_name,
                expected_key="category_names",
            )
            if self.merchant_name is not None:
                raise ValueError("merchant_name must be omitted for category options")
            if self.goal_id is not None:
                raise ValueError("goal_id must be omitted for category options")
        elif self.kind == "merchant":
            if self.merchant_name is None:
                raise ValueError("merchant_name is required for merchant options")
            if self.label is None:
                raise ValueError("label is required for merchant options")
            if self.payload_fragment is None:
                raise ValueError("payload_fragment is required for merchant options")
            self._validate_variant_alignment(
                variant_label="merchant",
                expected_label=self.merchant_name,
                expected_key="merchant_names",
            )
            if self.category_name is not None:
                raise ValueError("category_name must be omitted for merchant options")
            if self.goal_id is not None:
                raise ValueError("goal_id must be omitted for merchant options")
        elif self.kind == "goal":
            if self.goal_id is None:
                raise ValueError("goal_id is required for goal options")
            if self.title is None:
                raise ValueError("title is required for goal options")
            if self.label is None:
                raise ValueError("label is required for goal options")
            if self.category_name is not None:
                raise ValueError("category_name must be omitted for goal options")
            if self.merchant_name is not None:
                raise ValueError("merchant_name must be omitted for goal options")

    def _validate_variant_alignment(
        self,
        *,
        variant_label: str,
        expected_label: str,
        expected_key: str,
    ) -> None:
        if self.label != expected_label:
            raise ValueError(f"label must match {variant_label}_name")
        payload_fragment = self.payload_fragment or {}
        allowed_keys = {expected_key, "title"}
        if not set(payload_fragment).issubset(allowed_keys) or expected_key not in payload_fragment:
            raise ValueError(
                "payload_fragment must contain "
                f"{expected_key} and may include title for {variant_label} options"
            )
        if payload_fragment.get(expected_key) != [expected_label]:
            raise ValueError(f"payload_fragment must contain {expected_key}=[{expected_label!r}]")
        if "title" in payload_fragment and not isinstance(payload_fragment.get("title"), str):
            raise ValueError("payload_fragment title must be a string")


@dataclass(frozen=True)
class GoalCaptureResult:
    result_type: GoalCaptureResultType
    assistant_message: str | None = None
    action: GoalCaptureAction | None = None
    payload: dict[str, object] | None = None
    goal_id: int | None = None
    clarification_type: GoalCaptureClarificationType | None = None
    question: str | None = None
    options: list[GoalCaptureOption] | None = None
    resume_payload: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.result_type == "create":
            if self.action != "goal_create":
                raise ValueError("action must be goal_create for create results")
            if self.payload is None:
                raise ValueError("payload is required for create results")
            if self.assistant_message is None:
                raise ValueError("assistant_message is required for create results")
            self._require_absent(
                "goal_id",
                "clarification_type",
                "question",
                "options",
                "resume_payload",
            )
        elif self.result_type == "update":
            if self.action != "goal_update":
                raise ValueError("action must be goal_update for update results")
            if self.goal_id is None:
                raise ValueError("goal_id is required for update results")
            if self.payload is None:
                raise ValueError("payload is required for update results")
            if self.assistant_message is None:
                raise ValueError("assistant_message is required for update results")
            self._require_absent(
                "clarification_type",
                "question",
                "options",
                "resume_payload",
            )
        elif self.result_type == "clarify":
            if self.clarification_type is None:
                raise ValueError("clarification_type is required for clarify results")
            if self.question is None:
                raise ValueError("question is required for clarify results")
            if self.action is not None and self.action not in {"goal_create", "goal_update"}:
                raise ValueError("action must be goal_create or goal_update for clarify results")
            if self.clarification_type == "ambiguous_goal":
                if self.action != "goal_update":
                    raise ValueError(
                        "action must be goal_update for ambiguous_goal clarify results"
                    )
                if self.resume_payload is None:
                    raise ValueError(
                        "resume_payload is required for ambiguous_goal clarify results"
                    )
            if self.clarification_type == "ambiguous_subject":
                if self.action != "goal_create":
                    raise ValueError(
                        "action must be goal_create for ambiguous_subject clarify results"
                    )
                if self.resume_payload is None:
                    raise ValueError(
                        "resume_payload is required for ambiguous_subject clarify results"
                    )
            if self.clarification_type in {"ambiguous_goal", "ambiguous_subject"} and (
                self.options is None or not self.options
            ):
                raise ValueError(
                    "options are required for ambiguous_goal and ambiguous_subject clarify results"
                )
            if self.clarification_type == "missing_goal" and self.options is not None:
                raise ValueError("options must be omitted for missing_goal clarify results")
            self._require_absent("payload", "goal_id", "assistant_message")
        elif self.result_type == "no_match":
            if self.assistant_message is None:
                raise ValueError("assistant_message is required for no_match results")
            self._require_absent(
                "action",
                "payload",
                "goal_id",
                "clarification_type",
                "question",
                "options",
                "resume_payload",
            )
        else:
            raise ValueError("result_type is invalid")

    def _require_absent(self, *field_names: str) -> None:
        for field_name in field_names:
            if getattr(self, field_name) is not None:
                raise ValueError(f"{field_name} must be omitted for {self.result_type} results")


@dataclass(frozen=True)
class GoalProgress:
    """Derived goal progress for review and UI use.

    ``summary`` is human-facing convenience text. It is intentionally not a
    stable machine contract; downstream code should use the structured fields
    instead of parsing summary wording.
    """

    goal_id: int
    title: str
    metric_type: str
    target_value: int
    actual_value: int
    remaining_value: int | None
    current_start: str
    current_end: str
    status: str
    summary: str
    category_names: list[str]
    merchant_names: list[str]
    account_names: list[str]


__all__ = [
    "GoalCaptureAction",
    "GoalCaptureClarificationType",
    "GoalCaptureOption",
    "GoalCaptureOptionKind",
    "GoalCaptureResult",
    "GoalCaptureResultType",
    "GoalCreateInput",
    "GoalProgress",
    "GoalRecord",
    "GoalUpdateInput",
]
