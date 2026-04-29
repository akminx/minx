"""Regex-based natural-language goal capture parser.

This module implements a deliberately lightweight, rule-based parser for
conversational goal capture (e.g. "log last week's groceries" or "lower my
coffee spend below $40 a month"). It is intentionally narrow in scope: the
parser recognizes a curated set of phrasings that appear frequently in user
messages and produces structured :class:`GoalCaptureResult` responses without
depending on a language model.

Why regex first?
----------------
* **Determinism.** The most common flows ("create a grocery budget",
  "log the last transaction to this goal") must behave identically for the
  same input. Regex-driven logic gives us exact control over which phrasings
  map to which :class:`GoalCaptureResult` variants.
* **Latency and cost.** Common messages resolve in microseconds without
  reaching an LLM. This keeps the happy-path cheap and offline-friendly.
* **Trust boundary.** Natural-language input is untrusted. A focused regex
  parser makes it easier to validate amounts, merchants, and categories
  before they ever reach the goal service.

LLM fallback lives in :mod:`minx_mcp.core.goal_capture_llm` and is invoked by
:mod:`minx_mcp.core.goal_parse` only when the regex layer is unable to
confidently interpret the message.

Known limitations
-----------------
The regex layer intentionally trades recall for precision. Representative
phrasings that are *not* handled today and that callers should route to the
LLM fallback or a structured form:

* Multi-clause or compound requests ("add a grocery budget and pause my
  coffee goal"). Only a single intent per message is recognized.
* Relative dates beyond a small curated set ("log yesterday", "log last
  week" work; "log the Tuesday before last" does not).
* Free-form amounts in non-USD currencies, words instead of digits
  ("fifty dollars"), or ranges ("between $30 and $50").
* Spanish, Spanglish, or other non-English phrasings. All matchers assume
  English and are case-insensitive but not locale-aware.
* Merchant and category names that diverge from the user's stored catalog.
  We only resolve known merchants/categories; novel names return a
  clarification response rather than guessing.
* Typos and aggressive abbreviations beyond a small curated synonym table
  (e.g. "mo" → "month"). We do not perform fuzzy matching.

When adding new phrasings, prefer extending the structured helpers in
:mod:`minx_mcp.core.goal_capture_utils` and keeping this module focused on
pattern recognition. If a phrasing requires semantic understanding beyond
regex matching, route it through the LLM fallback instead of adding an
ever-growing pile of special cases here.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.goal_capture_utils import (
    _build_create_payload,
    _build_create_title,
    _compact_text,
    _contains_any_word,
    _contains_exact_phrase,
    _normalize_text,
    _summarize_goal_filters,
    _supported_conversational_goal,
)
from minx_mcp.core.models import (
    FinanceReadInterface,
    GoalCaptureOption,
    GoalCaptureResult,
    GoalRecord,
)
from minx_mcp.finance.normalization import normalize_merchant
from minx_mcp.money import parse_dollars_to_cents


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
    category_map = {_normalize_text(name): name for name in finance_api.list_goal_category_names()}
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
                if (
                    candidate_merchant is not None
                    and _normalize_text(candidate_merchant) == normalized_merchant_text
                ):
                    return candidate
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
    if (
        subject_text is not None
        and subject_resolution is not None
        and subject_resolution["kind"] != "ambiguous"
    ):
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


def _strip_create_period_suffix(subject: str) -> str:
    normalized_subject = subject.lower()
    for suffix in (" today", " this month", " this week", " monthly", " weekly"):
        if normalized_subject.endswith(suffix):
            return subject[: -len(suffix)].strip()
    explicit_start_match = re.search(
        r"\s+starting\s+\d{4}-\d{2}-\d{2}$", subject, flags=re.IGNORECASE
    )
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


def _goal_is_mentioned(goal: GoalRecord, normalized_message: str) -> bool:
    phrases = [goal.title, *goal.category_names, *goal.merchant_names, *goal.account_names]
    compact_message = _compact_text(normalized_message)
    for phrase in phrases:
        normalized_phrase = _normalize_text(phrase)
        if normalized_phrase and _contains_exact_phrase(normalized_message, normalized_phrase):
            return True
        if phrase in goal.merchant_names:
            normalized_merchant = normalize_merchant(phrase)
            if (
                normalized_merchant is not None
                and _compact_text(normalized_merchant) in compact_message
            ):
                return True
    return False


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

    candidates = [goal for goal in goals if _goal_is_mentioned(goal, normalized_message)]
    if not candidates:
        return GoalCaptureResult(
            result_type="clarify",
            action="goal_update",
            clarification_type="missing_goal",
            question="Which goal should I update?",
            resume_payload={"message": message, "review_date": review_date},
        )

    supported_candidates = [goal for goal in candidates if _supported_conversational_goal(goal)]
    if not supported_candidates:
        return GoalCaptureResult(
            result_type="no_match",
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
            raise RuntimeError(
                "payload must be set when update kind is not retarget_missing_target"
            )
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
    )
