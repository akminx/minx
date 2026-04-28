from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta
from difflib import get_close_matches
from typing import Protocol

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.interpretation.context import build_finance_query_context
from minx_mcp.core.interpretation.models import FinanceQueryInterpretation
from minx_mcp.core.interpretation.runner import (
    StructuredPromptLLMInterface,
    run_interpretation,
)
from minx_mcp.core.models import (
    FinanceQueryClarificationType,
    FinanceQueryFilters,
    FinanceQueryIntent,
    FinanceQueryPlan,
    JSONLLMInterface,
)
from minx_mcp.finance.normalization import normalize_merchant


class FinanceQueryReadProtocol(Protocol):
    def list_transaction_category_names(self) -> list[str]: ...
    def list_spending_merchant_names(self) -> list[str]: ...
    def list_account_names(self) -> list[str]: ...


async def interpret_finance_query(
    *,
    message: str,
    review_date: str,
    finance_api: FinanceQueryReadProtocol,
    llm: JSONLLMInterface | StructuredPromptLLMInterface,
) -> FinanceQueryPlan:
    _validate_iso_date(review_date, field_name="review_date")
    prompt = _render_finance_query_prompt(message, review_date, finance_api)
    raw = await run_interpretation(
        llm=llm,
        prompt=prompt,
        result_model=FinanceQueryInterpretation,
    )
    filters = _fill_deterministic_date_filters(
        message=message,
        review_date=review_date,
        filters=_to_filters(raw),
    )

    if raw.needs_clarification:
        return _llm_requested_clarification_plan(
            intent=raw.intent,
            filters=filters,
            confidence=raw.confidence,
            clarification_type=raw.clarification_type,
            llm_options=raw.options,
            finance_api=finance_api,
        )

    for field_name in ("start_date", "end_date"):
        value = getattr(filters, field_name)
        if value is not None:
            _validate_iso_date(value, field_name=field_name)

    category_name = _canonicalize_value(
        filters.category_name,
        finance_api.list_transaction_category_names(),
    )
    if filters.category_name is not None and category_name is None:
        return _clarify(
            intent=raw.intent,
            filters=filters,
            confidence=raw.confidence,
            clarification_type="unknown_category",
            question="Which category did you mean?",
            options=_suggest_options(
                filters.category_name, finance_api.list_transaction_category_names()
            ),
        )

    merchant = _canonicalize_value(filters.merchant, finance_api.list_spending_merchant_names())
    if filters.merchant is not None and merchant is None:
        return _clarify(
            intent=raw.intent,
            filters=filters,
            confidence=raw.confidence,
            clarification_type="unknown_merchant",
            question="Which merchant did you mean?",
            options=_suggest_options(filters.merchant, finance_api.list_spending_merchant_names()),
        )

    account_name = _canonicalize_value(filters.account_name, finance_api.list_account_names())
    if filters.account_name is not None and account_name is None:
        return _clarify(
            intent=raw.intent,
            filters=filters,
            confidence=raw.confidence,
            clarification_type="unknown_account",
            question="Which account did you mean?",
            options=_suggest_options(filters.account_name, finance_api.list_account_names()),
        )

    normalized_filters = replace(
        filters,
        category_name=category_name,
        merchant=merchant,
        account_name=account_name,
    )
    if raw.intent in {"sum_spending", "count_transactions"} and (
        normalized_filters.start_date is None or normalized_filters.end_date is None
    ):
        return _clarify(
            intent=raw.intent,
            filters=normalized_filters,
            confidence=raw.confidence,
            clarification_type="missing_date_range",
            question="Which date range should I use?",
        )

    return FinanceQueryPlan(
        intent=raw.intent,
        filters=normalized_filters,
        confidence=raw.confidence,
    )


def _render_finance_query_prompt(
    message: str,
    review_date: str,
    finance_api: FinanceQueryReadProtocol,
) -> str:
    ctx = build_finance_query_context(
        message=message,
        review_date=review_date,
        category_names=finance_api.list_transaction_category_names(),
        merchant_names=finance_api.list_spending_merchant_names(),
        account_names=finance_api.list_account_names(),
    )
    return "\n".join(
        [
            "Interpret the finance query request as JSON.",
            "Allowed intents: list_transactions, sum_spending, count_transactions.",
            (
                "Allowed filter keys: start_date, end_date, category_name, merchant, "
                "account_name, description_contains."
            ),
            (
                "Return keys: intent, filters, confidence, needs_clarification, "
                "clarification_type, question, options."
            ),
            f"Message: {ctx['message']}",
            f"Review date: {ctx['review_date']}",
            "Known categories: " + ", ".join(ctx["category_names"]),
            "Known merchants: " + ", ".join(ctx["merchant_names"]),
            "Known accounts: " + ", ".join(ctx["account_names"]),
        ]
    )


def _fill_deterministic_date_filters(
    *,
    message: str,
    review_date: str,
    filters: FinanceQueryFilters,
) -> FinanceQueryFilters:
    resolved = _resolve_date_filters_from_message(message, review_date)
    if resolved is None:
        return filters
    start_date, end_date = resolved
    return replace(
        filters,
        start_date=filters.start_date or start_date,
        end_date=filters.end_date or end_date,
    )


def _resolve_date_filters_from_message(
    message: str,
    review_date: str,
) -> tuple[str, str] | None:
    review_day = date.fromisoformat(review_date)
    normalized = message.casefold()

    explicit_range = re.search(
        r"\b(?:from|between)\s+(\d{4}-\d{2}-\d{2})\s+(?:to|and)\s+(\d{4}-\d{2}-\d{2})\b",
        message,
        flags=re.IGNORECASE,
    )
    if explicit_range is not None:
        return explicit_range.group(1), explicit_range.group(2)

    explicit_day = re.search(r"\bon\s+(\d{4}-\d{2}-\d{2})\b", message, flags=re.IGNORECASE)
    if explicit_day is not None:
        value = explicit_day.group(1)
        return value, value

    if "yesterday" in normalized:
        yesterday = review_day - timedelta(days=1)
        value = yesterday.isoformat()
        return value, value
    if "today" in normalized:
        value = review_day.isoformat()
        return value, value
    if "last week" in normalized:
        current_week_start = review_day - timedelta(days=review_day.weekday())
        start = current_week_start - timedelta(days=7)
        end = current_week_start - timedelta(days=1)
        return start.isoformat(), end.isoformat()
    if "this week" in normalized:
        start = review_day - timedelta(days=review_day.weekday())
        return start.isoformat(), review_day.isoformat()
    if "last month" in normalized:
        current_month_start = review_day.replace(day=1)
        end = current_month_start - timedelta(days=1)
        start = end.replace(day=1)
        return start.isoformat(), end.isoformat()
    if "this month" in normalized:
        start = review_day.replace(day=1)
        return start.isoformat(), review_day.isoformat()

    return None


def _to_filters(raw: FinanceQueryInterpretation) -> FinanceQueryFilters:
    return FinanceQueryFilters(
        start_date=raw.filters.start_date,
        end_date=raw.filters.end_date,
        category_name=raw.filters.category_name,
        merchant=raw.filters.merchant,
        account_name=raw.filters.account_name,
        description_contains=raw.filters.description_contains,
    )


def _llm_requested_clarification_plan(
    *,
    intent: FinanceQueryIntent,
    filters: FinanceQueryFilters,
    confidence: float,
    clarification_type: FinanceQueryClarificationType | None,
    llm_options: list[str] | None,
    finance_api: FinanceQueryReadProtocol,
) -> FinanceQueryPlan:
    if clarification_type is None:
        raise InvalidInputError("finance_query clarification_type is required")

    return _clarify(
        intent=intent,
        filters=filters,
        confidence=confidence,
        clarification_type=clarification_type,
        clarification_template=f"finance_query.clarify.{clarification_type}",
        clarification_slots=_clarification_slots(intent, filters, clarification_type),
        question=_deterministic_clarification_question(clarification_type),
        options=_deterministic_clarification_options(
            clarification_type, filters, finance_api, llm_options
        ),
    )


def _deterministic_clarification_question(
    clarification_type: FinanceQueryClarificationType,
) -> str:
    if clarification_type == "unknown_category":
        return "Which category did you mean?"
    if clarification_type in {"ambiguous_merchant", "unknown_merchant"}:
        return "Which merchant did you mean?"
    if clarification_type == "unknown_account":
        return "Which account did you mean?"
    if clarification_type == "missing_date_range":
        return "Which date range should I use?"
    raise InvalidInputError(f"Unsupported finance_query clarification_type: {clarification_type}")


def _deterministic_clarification_options(
    clarification_type: FinanceQueryClarificationType,
    filters: FinanceQueryFilters,
    finance_api: FinanceQueryReadProtocol,
    llm_options: list[str] | None = None,
) -> list[str] | None:
    if clarification_type == "unknown_category" and filters.category_name is not None:
        return _suggest_options(filters.category_name, finance_api.list_transaction_category_names())
    if clarification_type in {"ambiguous_merchant", "unknown_merchant"} and filters.merchant is not None:
        return _suggest_options(filters.merchant, finance_api.list_spending_merchant_names())
    if clarification_type == "unknown_account" and filters.account_name is not None:
        return _suggest_options(filters.account_name, finance_api.list_account_names())
    if clarification_type in {"ambiguous_merchant", "unknown_merchant"}:
        return _known_llm_options(llm_options, finance_api.list_spending_merchant_names())
    if clarification_type == "unknown_category":
        return _known_llm_options(llm_options, finance_api.list_transaction_category_names())
    if clarification_type == "unknown_account":
        return _known_llm_options(llm_options, finance_api.list_account_names())
    return None


def _clarification_slots(
    intent: FinanceQueryIntent,
    filters: FinanceQueryFilters,
    clarification_type: FinanceQueryClarificationType,
) -> dict[str, object]:
    slots: dict[str, object] = {
        "intent": intent,
        "filters": filters.to_public_dict(),
        "field": _clarification_field(clarification_type),
    }
    value = _clarification_field_value(filters, clarification_type)
    if value is not None:
        slots["value"] = value
    return slots


def _clarification_field(clarification_type: FinanceQueryClarificationType) -> str:
    if clarification_type in {"ambiguous_merchant", "unknown_merchant"}:
        return "merchant"
    if clarification_type == "unknown_category":
        return "category_name"
    if clarification_type == "unknown_account":
        return "account_name"
    if clarification_type == "missing_date_range":
        return "date_range"
    raise InvalidInputError(f"Unsupported finance_query clarification_type: {clarification_type}")


def _clarification_field_value(
    filters: FinanceQueryFilters,
    clarification_type: FinanceQueryClarificationType,
) -> str | None:
    if clarification_type in {"ambiguous_merchant", "unknown_merchant"}:
        return filters.merchant
    if clarification_type == "unknown_category":
        return filters.category_name
    if clarification_type == "unknown_account":
        return filters.account_name
    return None


def _known_llm_options(llm_options: list[str] | None, candidates: list[str]) -> list[str] | None:
    if not llm_options:
        return None
    candidate_by_key = {_normalize_lookup_value(candidate): candidate for candidate in candidates}
    grounded: list[str] = []
    for option in llm_options:
        known = candidate_by_key.get(_normalize_lookup_value(option))
        if known is not None and known not in grounded:
            grounded.append(known)
    return grounded or None


def _canonicalize_value(value: str | None, candidates: list[str]) -> str | None:
    if value is None:
        return None
    normalized_value = _normalize_lookup_value(value)
    for candidate in candidates:
        if _normalize_lookup_value(candidate) == normalized_value:
            return candidate
    normalized_merchant_value = normalize_merchant(value)
    if normalized_merchant_value is not None:
        merchant_lookup = _normalize_lookup_value(normalized_merchant_value)
        for candidate in candidates:
            candidate_merchant = normalize_merchant(candidate)
            if (
                candidate_merchant is not None
                and _normalize_lookup_value(candidate_merchant) == merchant_lookup
            ):
                return candidate
    return None


def _suggest_options(value: str, candidates: list[str]) -> list[str] | None:
    normalized = value.strip().casefold()
    substring_matches = [
        candidate for candidate in candidates if normalized in candidate.casefold()
    ]
    if substring_matches:
        return substring_matches[:5]
    matches = get_close_matches(value, candidates, n=5, cutoff=0.4)
    return matches or None


def _validate_iso_date(value: str, *, field_name: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInputError(f"{field_name} must be a valid ISO date") from exc


def _normalize_lookup_value(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _clarify(
    *,
    intent: FinanceQueryIntent,
    filters: FinanceQueryFilters,
    confidence: float,
    clarification_type: FinanceQueryClarificationType,
    question: str,
    clarification_template: str | None = None,
    clarification_slots: dict[str, object] | None = None,
    options: list[str] | None = None,
) -> FinanceQueryPlan:
    return FinanceQueryPlan(
        intent=intent,
        filters=filters,
        confidence=confidence,
        needs_clarification=True,
        clarification_type=clarification_type,
        clarification_template=clarification_template
        or f"finance_query.clarify.{clarification_type}",
        clarification_slots=clarification_slots
        or _clarification_slots(intent, filters, clarification_type),
        question=question,
        options=options,
    )
