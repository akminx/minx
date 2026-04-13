from __future__ import annotations

import logging
import re
from datetime import date, timedelta

logger = logging.getLogger(__name__)
from typing import cast

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.interpretation.context import build_goal_capture_context
from minx_mcp.core.interpretation.models import GoalCaptureInterpretation
from minx_mcp.core.interpretation.runner import run_interpretation
from minx_mcp.core.goals import (
    GoalService,
    normalize_filter_names,
    normalize_goal_type,
    normalize_title,
    validate_goal_state,
)
from minx_mcp.core.models import (
    FinanceReadInterface,
    GoalCaptureOption,
    GoalCaptureResult,
    GoalCreateInput,
    GoalRecord,
    GoalUpdateInput,
)
from minx_mcp.finance.normalization import normalize_merchant
from minx_mcp.money import format_cents, parse_dollars_to_cents

_SUPPORTED_CREATE_GOAL_TYPE = "spending_cap"
_SUPPORTED_CREATE_METRIC_TYPE = "sum_below"
_SUPPORTED_CREATE_DOMAIN = "finance"
_SUPPORTED_UPDATE_STATUSES = {"active", "paused"}


async def parse_goal_input(
    *,
    finance_api: FinanceReadInterface,
    goal_service: GoalService,
    review_date: str,
    goals,
    message: str | None = None,
    structured_input: dict | None = None,
    llm: object | None = None,
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
    llm: object | None = None,
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


async def _capture_with_llm(
    *,
    message: str,
    review_date: str,
    finance_api: FinanceReadInterface,
    goals: list[GoalRecord],
    llm: object,
) -> GoalCaptureResult | None:
    prompt = _render_goal_capture_prompt(message, review_date, finance_api, goals)
    try:
        interpretation = await _run_goal_capture_interpretation(llm, prompt)
    except Exception as exc:
        logger.warning("LLM goal capture failed, falling back to regex: %s", type(exc).__name__)
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


async def _run_goal_capture_interpretation(llm: object, prompt: str) -> GoalCaptureInterpretation:
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


def _resolve_exact_subject(
    subject_kind: str,
    subject: str,
    finance_api: FinanceReadInterface,
) -> str | None:
    candidates = (
        finance_api.list_goal_category_names()
        if subject_kind == "category"
        else finance_api.list_spending_merchant_names()
    )
    normalized_subject = _normalize_text(subject)
    for candidate in candidates:
        if _normalize_text(candidate) == normalized_subject:
            return candidate
    if subject_kind == "merchant":
        normalized_merchant = normalize_merchant(subject)
        if normalized_merchant is not None:
            normalized_merchant_text = _normalize_text(normalized_merchant)
            for candidate in candidates:
                candidate_merchant = normalize_merchant(candidate)
                if candidate_merchant is not None and _normalize_text(candidate_merchant) == normalized_merchant_text:
                    return candidate
    return None


def _supported_conversational_goal(goal: GoalRecord) -> bool:
    return (
        goal.goal_type == _SUPPORTED_CREATE_GOAL_TYPE
        and goal.metric_type == _SUPPORTED_CREATE_METRIC_TYPE
        and goal.domain == _SUPPORTED_CREATE_DOMAIN
        and goal.status in _SUPPORTED_UPDATE_STATUSES
    )


def _capture_create(
    *,
    message: str,
    normalized_message: str,
    review_date: str,
    finance_api: FinanceReadInterface,
) -> GoalCaptureResult | None:
    if not _looks_like_create_message(normalized_message):
        return None

    subject_text = _extract_subject_phrase(message)
    period = _resolve_create_period(normalized_message)
    starts_on = _resolve_starts_on(review_date, message, normalized_message, period)
    if subject_text is None:
        return _build_vague_intent_clarify()
    subject_resolution = _resolve_subject(subject_text, finance_api)
    if subject_resolution is None:
        return _build_vague_intent_clarify()
    amount_match = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", message)
    if amount_match is None:
        return _build_missing_target_clarify(
            message=message,
            review_date=review_date,
            subject_text=subject_text,
            subject_resolution=subject_resolution,
            period=period,
            starts_on=starts_on,
        )
    target_value = parse_dollars_to_cents(amount_match.group(1))
    resume_payload = _build_create_payload(
        subject=subject_text.strip(),
        period=period,
        starts_on=starts_on,
        target_value=target_value,
    )
    if subject_resolution["kind"] == "ambiguous":
        category_name = subject_resolution["category"]
        merchant_name = subject_resolution["merchant"]
        return GoalCaptureResult(
            result_type="clarify",
            action="goal_create",
            clarification_type="ambiguous_subject",
            question="Do you mean the category or the merchant?",
            options=[
                GoalCaptureOption(
                    kind="category",
                    label=category_name,
                    category_name=category_name,
                    filter_summary=f"category_names=[{category_name!r}]",
                    payload_fragment={
                        "title": _build_create_title(category_name),
                        "category_names": [category_name],
                    },
                ),
                GoalCaptureOption(
                    kind="merchant",
                    label=merchant_name,
                    merchant_name=merchant_name,
                    filter_summary=f"merchant_names=[{merchant_name!r}]",
                    payload_fragment={
                        "title": _build_create_title(merchant_name),
                        "merchant_names": [merchant_name],
                    },
                ),
            ],
            resume_payload=resume_payload,
        )

    canonical_subject = subject_resolution["subject"]
    payload = _build_create_payload(
        subject=canonical_subject,
        period=period,
        starts_on=starts_on,
        target_value=target_value,
    )
    if subject_resolution["kind"] == "category":
        payload["category_names"] = [canonical_subject]
    else:
        payload["merchant_names"] = [canonical_subject]

    return GoalCaptureResult(
        result_type="create",
        action="goal_create",
        payload=payload,
        assistant_message=_build_create_assistant_message(canonical_subject),
    )


def _capture_update(
    *,
    normalized_message: str,
    message: str,
    review_date: str,
    goals: list[GoalRecord],
) -> GoalCaptureResult | None:
    _update_kind, payload = _resolve_update_payload(normalized_message, message)
    if _update_kind is None:
        return None

    candidates = [
        goal
        for goal in goals
        if _goal_is_mentioned(goal, normalized_message)
    ]
    if not candidates:
        return GoalCaptureResult(
            result_type="clarify",
            action="goal_update",
            clarification_type="missing_goal",
            question="Which goal should I update?",
            resume_payload={"message": message, "review_date": review_date},
        )

    supported_candidates = [
        goal for goal in candidates if _supported_conversational_goal(goal)
    ]
    if not supported_candidates:
        return GoalCaptureResult(
            result_type="no_match",
            assistant_message="That goal family is not supported for conversational updates.",
        )
    if _update_kind == "retarget_missing_target":
        if len(supported_candidates) == 1:
            return _build_missing_update_target_clarify(
                goal=supported_candidates[0],
                message=message,
                review_date=review_date,
            )
        return _build_missing_update_target_clarify(
            goal=None,
            message=message,
            review_date=review_date,
        )
    if len(supported_candidates) > 1:
        if payload is None:
            raise RuntimeError("payload must be set when update kind is not retarget_missing_target")
        return GoalCaptureResult(
            result_type="clarify",
            action="goal_update",
            clarification_type="ambiguous_goal",
            question="Which goal do you mean?",
            options=[
                GoalCaptureOption(
                    goal_id=goal.id,
                    title=goal.title,
                    status=goal.status,
                    period=goal.period,
                    target_value=goal.target_value,
                    filter_summary=_summarize_goal_filters(goal),
                    kind="goal",
                    label=goal.title,
                )
                for goal in supported_candidates
            ],
            resume_payload=payload,
        )

    goal = supported_candidates[0]
    if payload is None:
        raise RuntimeError("payload must be set when update kind is not retarget_missing_target")
    return GoalCaptureResult(
        result_type="update",
        action="goal_update",
        goal_id=goal.id,
        payload=payload,
        assistant_message=_build_update_assistant_message(goal, payload),
    )


def _resolve_update_payload(
    normalized_message: str,
    message: str,
) -> tuple[str | None, dict[str, object] | None]:
    if _contains_any_word(normalized_message, ("unpause", "resume")):
        return "resume", {"status": "active"}
    if _contains_any_word(normalized_message, ("pause",)):
        return "pause", {"status": "paused"}
    if _contains_any_word(normalized_message, ("archive",)):
        return "archive", {"status": "archived"}
    if _contains_any_word(normalized_message, ("retarget", "change", "set")):
        amount_match = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", message)
        if amount_match is None:
            return "retarget_missing_target", None
        return "retarget", {"target_value": parse_dollars_to_cents(amount_match.group(1))}
    return None, None


def _build_create_payload(
    *,
    subject: str,
    period: str,
    starts_on: str,
    target_value: int,
) -> dict[str, object]:
    return {
        "goal_type": _SUPPORTED_CREATE_GOAL_TYPE,
        "title": _build_create_title(subject),
        "metric_type": _SUPPORTED_CREATE_METRIC_TYPE,
        "target_value": target_value,
        "period": period,
        "domain": _SUPPORTED_CREATE_DOMAIN,
        "category_names": [],
        "merchant_names": [],
        "account_names": [],
        "starts_on": starts_on,
        "ends_on": None,
        "notes": None,
    }


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


def _summarize_goal_filters(goal: GoalRecord) -> str:
    if goal.category_names:
        return f"category_names={goal.category_names!r}"
    if goal.merchant_names:
        return f"merchant_names={goal.merchant_names!r}"
    if goal.account_names:
        return f"account_names={goal.account_names!r}"
    return "all spending"


def _goal_is_mentioned(goal: GoalRecord, normalized_message: str) -> bool:
    phrases = [goal.title, *goal.category_names, *goal.merchant_names, *goal.account_names]
    compact_message = _compact_text(normalized_message)
    for phrase in phrases:
        normalized_phrase = _normalize_text(phrase)
        if normalized_phrase and _contains_exact_phrase(normalized_message, normalized_phrase):
            return True
        if phrase in goal.merchant_names:
            normalized_merchant = normalize_merchant(phrase)
            if normalized_merchant is not None and _compact_text(normalized_merchant) in compact_message:
                return True
    return False


def _looks_like_create_message(normalized_message: str) -> bool:
    return any(
        token in normalized_message
        for token in (
            "spend less than",
            "spend under",
            "spend below",
            "make a goal",
            "create a goal",
            "set a goal",
        )
    )


def _extract_subject_phrase(message: str) -> str | None:
    patterns = [
        r"\b(?:on|at|for)\s+(?P<subject>.+?)\s+(?:today|this\s+month|this\s+week|monthly|weekly|starting\s+\d{4}-\d{2}-\d{2})\b",
        r"\b(?:on|at|for)\s+(?P<subject>.+?)\s+(?:to|under|below|less\s+than)\b",
        r"\b(?:on|at|for)\s+(?P<subject>.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match is not None:
            subject = _strip_create_period_suffix(match.group("subject").strip())
            if subject:
                return subject
    return None


def _resolve_subject(
    subject_text: str,
    finance_api: FinanceReadInterface,
) -> dict[str, str] | None:
    normalized_subject = _normalize_text(subject_text)
    category_map = {
        _normalize_text(name): name for name in finance_api.list_goal_category_names()
    }
    merchant_map = {
        _normalize_text(name): name for name in finance_api.list_spending_merchant_names()
    }
    category = category_map.get(normalized_subject)
    merchant = merchant_map.get(normalized_subject)
    if merchant is None:
        normalized_merchant = normalize_merchant(subject_text)
        if normalized_merchant is not None:
            merchant = merchant_map.get(_normalize_text(normalized_merchant))
    if category is not None and merchant is not None:
        return {"kind": "ambiguous", "category": category, "merchant": merchant}
    if category is not None:
        return {"kind": "category", "subject": category}
    if merchant is not None:
        return {"kind": "merchant", "subject": merchant}
    return None


def _resolve_create_period(normalized_message: str) -> str:
    if "today" in normalized_message:
        return "daily"
    if "this week" in normalized_message or "weekly" in normalized_message:
        return "weekly"
    return "monthly"


def _resolve_starts_on(
    review_date: str,
    message: str,
    normalized_message: str,
    period: str,
) -> str:
    explicit_date = _extract_iso_date(message)
    if explicit_date is not None:
        return explicit_date

    review_day = date.fromisoformat(review_date)
    if "today" in normalized_message or period == "daily":
        return review_day.isoformat()
    if period == "weekly":
        if "this week" in normalized_message or "weekly" in normalized_message:
            return (review_day - timedelta(days=review_day.weekday())).isoformat()
        return review_day.isoformat()
    if "this month" in normalized_message or "monthly" in normalized_message:
        return review_day.replace(day=1).isoformat()
    return review_day.isoformat()


def _build_missing_target_clarify(
    *,
    message: str,
    review_date: str,
    subject_text: str | None,
    subject_resolution: dict[str, str] | None,
    period: str,
    starts_on: str,
) -> GoalCaptureResult:
    resume_payload: dict[str, object] = {
        "message": message,
        "review_date": review_date,
        "period": period,
        "starts_on": starts_on,
    }
    if subject_text is not None and subject_resolution is not None and subject_resolution["kind"] != "ambiguous":
        canonical_subject = subject_resolution["subject"]
        payload = _build_create_payload(
            subject=canonical_subject,
            period=period,
            starts_on=starts_on,
            target_value=1,
        )
        if subject_resolution["kind"] == "category":
            payload["category_names"] = [canonical_subject]
        else:
            payload["merchant_names"] = [canonical_subject]
        payload.pop("target_value", None)
        resume_payload = payload
    return GoalCaptureResult(
        result_type="clarify",
        action="goal_create",
        clarification_type="missing_target",
        question="How much should the goal target be?",
        resume_payload=resume_payload,
    )


def _build_ambiguous_subject_clarify(
    *,
    category_name: str,
    merchant_name: str,
    period: str,
    starts_on: str,
    target_value: int,
) -> GoalCaptureResult:
    return GoalCaptureResult(
        result_type="clarify",
        action="goal_create",
        clarification_type="ambiguous_subject",
        question="Do you mean the category or the merchant?",
        options=[
            GoalCaptureOption(
                kind="category",
                label=category_name,
                category_name=category_name,
                filter_summary=f"category_names=[{category_name!r}]",
                payload_fragment={
                    "title": _build_create_title(category_name),
                    "category_names": [category_name],
                },
            ),
            GoalCaptureOption(
                kind="merchant",
                label=merchant_name,
                merchant_name=merchant_name,
                filter_summary=f"merchant_names=[{merchant_name!r}]",
                payload_fragment={
                    "title": _build_create_title(merchant_name),
                    "merchant_names": [merchant_name],
                },
            ),
        ],
        resume_payload=_build_create_payload(
            subject=category_name,
            period=period,
            starts_on=starts_on,
            target_value=target_value,
        ),
    )


def _build_missing_update_target_clarify(
    *,
    goal: GoalRecord | None,
    message: str,
    review_date: str,
) -> GoalCaptureResult:
    if goal is None:
        return GoalCaptureResult(
            result_type="clarify",
            clarification_type="missing_target",
            question="What should the new target be?",
        )
    return GoalCaptureResult(
        result_type="clarify",
        action="goal_update",
        clarification_type="missing_target",
        question="What should the new target be?",
        resume_payload={
            "goal_id": goal.id,
            "message": message,
            "review_date": review_date,
        },
    )


def _build_vague_intent_clarify() -> GoalCaptureResult:
    return GoalCaptureResult(
        result_type="clarify",
        clarification_type="vague_intent",
        question="What spending category or merchant should this goal track?",
    )


def _build_create_assistant_message(subject: str) -> str:
    return f"I can create {subject} Spending Cap."


def _build_update_assistant_message(goal: GoalRecord, payload: dict[str, object]) -> str:
    status = payload.get("status")
    if status == "paused":
        return f"I can pause {goal.title}."
    if status == "active":
        return f"I can resume {goal.title}."
    if status == "archived":
        return f"I can archive {goal.title}."
    if "target_value" in payload:
        target_value = cast(int, payload["target_value"])
        return f"I can update {goal.title} to {format_cents(target_value)}."
    return f"I can update {goal.title}."


def _strip_create_period_suffix(subject: str) -> str:
    normalized_subject = subject.lower()
    for suffix in (" today", " this month", " this week", " monthly", " weekly"):
        if normalized_subject.endswith(suffix):
            return subject[: -len(suffix)].strip()
    explicit_start_match = re.search(r"\s+starting\s+\d{4}-\d{2}-\d{2}$", subject, flags=re.IGNORECASE)
    if explicit_start_match is not None:
        return subject[: explicit_start_match.start()].strip()
    return subject


def _extract_iso_date(normalized_message: str) -> str | None:
    match = re.search(r"\b([0-9]{4}-[0-9]{2}-[0-9]{2})\b", normalized_message)
    if match is None:
        return None
    explicit_date = match.group(1)
    try:
        date.fromisoformat(explicit_date)
    except ValueError as exc:
        raise InvalidInputError("explicit start date must be a valid ISO date") from exc
    return explicit_date


def _build_create_title(subject: str) -> str:
    return f"{subject} Spending Cap"


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _compact_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _contains_any_word(haystack: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", haystack) for word in words)


def _contains_exact_phrase(haystack: str, needle: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack))


def _validate_structured_goal_input(
    *,
    structured_input: dict[str, object],
    finance_api: FinanceReadInterface,
    goal_service: GoalService,
) -> GoalCaptureResult:
    action = structured_input.get("action")
    payload = structured_input.get("payload")
    if action not in {"goal_create", "goal_update"}:
        raise InvalidInputError("structured_input.action must be goal_create or goal_update")
    if not isinstance(payload, dict):
        raise InvalidInputError("structured_input.payload must be an object")

    if action == "goal_create":
        create_result = _validate_structured_create_payload(payload, finance_api)
        if create_result is not None:
            return create_result
        return GoalCaptureResult(
            result_type="create",
            action="goal_create",
            payload=payload,
            assistant_message="I can create that goal.",
        )

    goal_id = structured_input.get("goal_id")
    if not isinstance(goal_id, int):
        raise InvalidInputError("structured_input.goal_id must be provided for updates")
    if "goal_id" in payload:
        raise InvalidInputError("goal_id must not appear inside payload")
    goal = goal_service.get_goal(goal_id)
    if not _supported_conversational_goal(goal):
        return GoalCaptureResult(
            result_type="no_match",
            assistant_message="That goal family is not supported for conversational updates.",
        )
    _validate_structured_update_payload(payload, goal_service=goal_service, goal_id=goal_id)
    return GoalCaptureResult(
        result_type="update",
        action="goal_update",
        goal_id=goal_id,
        payload=payload,
        assistant_message=f"I can update {goal.title}.",
    )


def _validate_structured_create_payload(
    payload: dict[str, object],
    finance_api: FinanceReadInterface,
) -> GoalCaptureResult | None:
    _validate_goal_create_input_shape(payload)
    goal_type = payload.get("goal_type")
    metric_type = payload.get("metric_type")
    domain = payload.get("domain")
    if not (
        goal_type == "spending_cap"
        and metric_type == "sum_below"
        and domain == "finance"
    ):
        return GoalCaptureResult(
            result_type="no_match",
            assistant_message="That goal family is not supported for conversational creation.",
        )

    validate_goal_state(
        goal_type=normalize_goal_type(_require_str(payload, "goal_type")),
        title=normalize_title(_require_str(payload, "title")),
        target_value=_require_int(payload, "target_value"),
        metric_type=_require_str(payload, "metric_type"),
        period=_require_str(payload, "period"),
        domain=_require_str(payload, "domain"),
        starts_on=_require_str(payload, "starts_on"),
        ends_on=_require_optional_str(payload, "ends_on"),
        category_names=normalize_filter_names(
            _require_str_list(payload, "category_names"), "category_names"
        ),
        merchant_names=normalize_filter_names(
            _require_str_list(payload, "merchant_names"), "merchant_names"
        ),
        account_names=normalize_filter_names(
            _require_str_list(payload, "account_names"), "account_names"
        ),
    )

    _validate_canonical_names(
        finance_api.list_goal_category_names(),
        payload.get("category_names"),
        kind="category_names",
    )
    _validate_canonical_names(
        finance_api.list_spending_merchant_names(),
        payload.get("merchant_names"),
        kind="merchant_names",
        resolve=True,
        finance_api=finance_api,
    )
    _validate_canonical_names(
        finance_api.list_account_names(),
        payload.get("account_names"),
        kind="account_names",
    )
    return None


def _validate_structured_update_payload(
    payload: dict[str, object],
    *,
    goal_service: GoalService,
    goal_id: int,
) -> None:
    _validate_goal_update_input_shape(payload)
    goal = goal_service.get_goal(goal_id)
    next_title = goal.title if "title" not in payload else normalize_title(_require_str(payload, "title"))
    next_target_value = (
        goal.target_value if "target_value" not in payload else _require_int(payload, "target_value")
    )
    next_status = goal.status if "status" not in payload else _require_str(payload, "status")
    clear_ends_on = payload.get("clear_ends_on", False)
    if not isinstance(clear_ends_on, bool):
        raise InvalidInputError("clear_ends_on must be a boolean")
    next_ends_on = (
        None
        if clear_ends_on
        else _require_optional_str(payload, "ends_on")
        if "ends_on" in payload
        else goal.ends_on
    )
    validate_goal_state(
        goal_type=goal.goal_type,
        title=next_title,
        target_value=next_target_value,
        metric_type=goal.metric_type,
        period=goal.period,
        domain=goal.domain,
        starts_on=goal.starts_on,
        ends_on=next_ends_on,
        category_names=goal.category_names,
        merchant_names=goal.merchant_names,
        account_names=goal.account_names,
        status=next_status,
    )


def _validate_goal_create_input_shape(payload: dict[str, object]) -> None:
    _parse_goal_create_input(payload)


def _validate_goal_update_input_shape(payload: dict[str, object]) -> None:
    _parse_goal_update_input(payload)


def _parse_goal_create_input(payload: dict[str, object]) -> GoalCreateInput:
    required_keys = {
        "goal_type",
        "title",
        "metric_type",
        "target_value",
        "period",
        "domain",
        "category_names",
        "merchant_names",
        "account_names",
        "starts_on",
        "ends_on",
        "notes",
    }
    _require_exact_keys(payload, required_keys, context="goal_create")
    return GoalCreateInput(
        goal_type=_require_str(payload, "goal_type"),
        title=_require_str(payload, "title"),
        metric_type=_require_str(payload, "metric_type"),
        target_value=_require_int(payload, "target_value"),
        period=_require_str(payload, "period"),
        domain=_require_str(payload, "domain"),
        category_names=_require_str_list(payload, "category_names"),
        merchant_names=_require_str_list(payload, "merchant_names"),
        account_names=_require_str_list(payload, "account_names"),
        starts_on=_require_str(payload, "starts_on"),
        ends_on=_require_optional_str(payload, "ends_on"),
        notes=_require_optional_str(payload, "notes"),
    )


def _parse_goal_update_input(payload: dict[str, object]) -> GoalUpdateInput:
    allowed_keys = {
        "title",
        "target_value",
        "status",
        "ends_on",
        "notes",
        "clear_ends_on",
        "clear_notes",
    }
    _reject_unknown_keys(payload, allowed_keys, context="goal_update")
    return GoalUpdateInput(
        title=_require_optional_str(payload, "title") if "title" in payload else None,
        target_value=_require_int(payload, "target_value") if "target_value" in payload else None,
        status=_require_optional_str(payload, "status") if "status" in payload else None,
        ends_on=_require_optional_str(payload, "ends_on") if "ends_on" in payload else None,
        notes=_require_optional_str(payload, "notes") if "notes" in payload else None,
        clear_ends_on=_require_bool(payload, "clear_ends_on", default=False),
        clear_notes=_require_bool(payload, "clear_notes", default=False),
    )


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise InvalidInputError(f"{key} must be a string")
    return value


def _require_optional_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInputError(f"{key} must be a string when provided")
    return value


def _require_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError(f"{key} must be an integer")
    return value


def _require_bool(payload: dict[str, object], key: str, *, default: bool) -> bool:
    if key not in payload:
        return default
    value = payload[key]
    if not isinstance(value, bool):
        raise InvalidInputError(f"{key} must be a boolean")
    return value


def _require_str_list(payload: dict[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidInputError(f"{key} must be a list of strings")
    return value


def _validate_canonical_names(
    valid_names: list[str],
    names: object,
    *,
    kind: str,
    resolve: bool = False,
    finance_api: FinanceReadInterface | None = None,
) -> None:
    if names is None:
        return
    if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
        raise InvalidInputError(f"{kind} must be a list of strings")
    for name in names:
        if name not in valid_names:
            if resolve and finance_api is not None:
                resolved = _resolve_exact_subject("merchant", name, finance_api)
                if resolved is not None:
                    continue
            raise InvalidInputError(f"{kind} must contain canonical names only")


def _require_exact_keys(
    payload: dict[str, object],
    required_keys: set[str],
    *,
    context: str,
) -> None:
    missing_keys = required_keys - set(payload)
    if missing_keys:
        missing_list = ", ".join(sorted(missing_keys))
        raise InvalidInputError(f"{context} payload is missing required fields: {missing_list}")
    _reject_unknown_keys(payload, required_keys, context=context)


def _reject_unknown_keys(
    payload: dict[str, object],
    allowed_keys: set[str],
    *,
    context: str,
) -> None:
    unknown_keys = set(payload) - allowed_keys
    if unknown_keys:
        unknown_list = ", ".join(sorted(unknown_keys))
        raise InvalidInputError(f"{context} payload has unknown fields: {unknown_list}")
