from __future__ import annotations

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.goal_capture_llm import _capture_with_llm
from minx_mcp.core.goal_capture_nl import (
    _capture_create,
    _capture_update,
    _normalize_text,
    _resolve_exact_subject,
)
from minx_mcp.core.goal_capture_structured import _validate_structured_goal_input
from minx_mcp.core.goals import GoalService
from minx_mcp.core.models import (
    FinanceReadInterface,
    GoalCaptureResult,
    GoalRecord,
    JSONLLMInterface,
)


async def parse_goal_input(
    *,
    finance_api: FinanceReadInterface,
    goal_service: GoalService,
    review_date: str,
    goals: list[GoalRecord],
    message: str | None = None,
    structured_input: dict[str, object] | None = None,
    llm: JSONLLMInterface | None = None,
) -> GoalCaptureResult:
    if (message is None) == (structured_input is None):
        raise InvalidInputError("exactly one of message or structured_input must be provided")
    if message is not None:
        return await capture_goal_message(
            message=message,
            review_date=review_date,
            finance_api=finance_api,
            goals=goals,
            llm=llm,
        )
    if not isinstance(structured_input, dict):
        raise InvalidInputError("structured_input must be an object")
    return _validate_structured_goal_input(
        structured_input=structured_input,
        finance_api=finance_api,
        goal_service=goal_service,
    )


async def capture_goal_message(
    *,
    message: str,
    review_date: str,
    finance_api: FinanceReadInterface,
    goals: list[GoalRecord],
    llm: JSONLLMInterface | None = None,
) -> GoalCaptureResult:
    if llm is not None:
        interpreted = await _capture_with_llm(
            message=message,
            review_date=review_date,
            finance_api=finance_api,
            goals=goals,
            llm=llm,
        )
        if interpreted is not None:
            return interpreted

    normalized_message = _normalize_text(message)

    create_result = _capture_create(
        message=message,
        normalized_message=normalized_message,
        review_date=review_date,
        finance_api=finance_api,
    )
    if create_result is not None:
        return create_result

    update_result = _capture_update(
        normalized_message=normalized_message,
        message=message,
        review_date=review_date,
        goals=goals,
    )
    if update_result is not None:
        return update_result

    return GoalCaptureResult(
        result_type="no_match",
        assistant_message="I couldn't map that to a supported finance goal action.",
    )


# Re-export for external callers that import directly from goal_parse
__all__ = [
    "_resolve_exact_subject",
    "capture_goal_message",
    "parse_goal_input",
]
