from __future__ import annotations

import logging

from minx_mcp.core.goal_capture_nl import (
    _build_ambiguous_subject_clarify,
    _build_create_assistant_message,
    _build_update_assistant_message,
    _extract_subject_phrase,
    _resolve_exact_subject,
    _resolve_starts_on,
)
from minx_mcp.core.goal_capture_utils import (
    _build_create_payload,
    _normalize_text,
    _supported_conversational_goal,
)
from minx_mcp.core.interpretation.context import build_goal_capture_context
from minx_mcp.core.interpretation.models import GoalCaptureInterpretation
from minx_mcp.core.interpretation.runner import run_interpretation
from minx_mcp.core.models import (
    FinanceReadInterface,
    GoalCaptureResult,
    GoalRecord,
    JSONLLMInterface,
)

logger = logging.getLogger(__name__)


async def _capture_with_llm(
    *,
    message: str,
    review_date: str,
    finance_api: FinanceReadInterface,
    goals: list[GoalRecord],
    llm: JSONLLMInterface,
) -> GoalCaptureResult | None:
    prompt = _render_goal_capture_prompt(message, review_date, finance_api, goals)
    try:
        interpretation = await _run_goal_capture_interpretation(llm, prompt)
    except Exception as exc:
        logger.warning(
            "LLM goal capture failed, falling back to regex: %s: %s",
            type(exc).__name__,
            exc,
        )
        return None

    if interpretation.intent == "update":
        return _build_llm_update_result(interpretation, goals)
    if interpretation.intent != "create":
        return None
    if interpretation.subject_kind not in {"category", "merchant"}:
        return None
    if not interpretation.subject:
        return None
    if interpretation.period not in {"daily", "weekly", "monthly"}:
        return None
    if interpretation.target_value is None or interpretation.target_value <= 0:
        return None

    subject_text = _extract_subject_phrase(message)
    if subject_text is not None:
        from minx_mcp.core.goal_capture_nl import _resolve_subject

        deterministic_subject = _resolve_subject(subject_text, finance_api)
        if deterministic_subject is not None and deterministic_subject["kind"] == "ambiguous":
            return _build_ambiguous_subject_clarify(
                category_name=deterministic_subject["category"],
                merchant_name=deterministic_subject["merchant"],
                period=interpretation.period,
                starts_on=_resolve_starts_on(
                    review_date,
                    message,
                    _normalize_text(message),
                    interpretation.period,
                ),
                target_value=interpretation.target_value,
            )

    canonical_subject = _resolve_exact_subject(
        interpretation.subject_kind,
        interpretation.subject,
        finance_api,
    )
    if canonical_subject is None:
        return None

    payload = _build_create_payload(
        subject=canonical_subject,
        period=interpretation.period,
        starts_on=_resolve_starts_on(
            review_date,
            message,
            _normalize_text(message),
            interpretation.period,
        ),
        target_value=interpretation.target_value,
    )
    if interpretation.subject_kind == "category":
        payload["category_names"] = [canonical_subject]
    else:
        payload["merchant_names"] = [canonical_subject]
    return GoalCaptureResult(
        result_type="create",
        action="goal_create",
        payload=payload,
        assistant_message=_build_create_assistant_message(canonical_subject),
    )


async def _run_goal_capture_interpretation(
    llm: JSONLLMInterface,
    prompt: str,
) -> GoalCaptureInterpretation:
    return await run_interpretation(
        llm=llm,
        prompt=prompt,
        result_model=GoalCaptureInterpretation,
    )


def _render_goal_capture_prompt(
    message: str,
    review_date: str,
    finance_api: FinanceReadInterface,
    goals: list[GoalRecord],
) -> str:
    ctx = build_goal_capture_context(
        message=message,
        review_date=review_date,
        active_goals=goals,
        category_names=finance_api.list_goal_category_names(),
        merchant_names=finance_api.list_spending_merchant_names(),
    )
    return "\n".join(
        [
            "Interpret the goal capture request as JSON.",
            (
                "Return keys: intent, confidence, subject_kind, subject, period, "
                "target_value, update_kind, goal_id."
            ),
            f"Message: {ctx['message']}",
            f"Review date: {ctx['review_date']}",
            f"Candidate goals: {ctx['active_goals']}",
            "Known categories: " + ", ".join(ctx["category_names"]),
            "Known merchants: " + ", ".join(ctx["merchant_names"]),
        ]
    )


def _build_llm_update_result(
    interpretation: GoalCaptureInterpretation,
    goals: list[GoalRecord],
) -> GoalCaptureResult | None:
    if interpretation.goal_id is None or interpretation.update_kind is None:
        return None

    goal = next((candidate for candidate in goals if candidate.id == interpretation.goal_id), None)
    if goal is None or not _supported_conversational_goal(goal):
        return None

    if interpretation.update_kind == "pause":
        payload: dict[str, object] = {"status": "paused"}
    elif interpretation.update_kind == "resume":
        payload = {"status": "active"}
    elif interpretation.update_kind == "archive":
        payload = {"status": "archived"}
    elif interpretation.update_kind == "retarget":
        if interpretation.target_value is None or interpretation.target_value <= 0:
            return None
        payload = {"target_value": interpretation.target_value}
    else:
        return None

    return GoalCaptureResult(
        result_type="update",
        action="goal_update",
        goal_id=goal.id,
        payload=payload,
        assistant_message=_build_update_assistant_message(goal, payload),
    )
