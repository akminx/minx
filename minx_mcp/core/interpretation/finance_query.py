from __future__ import annotations

from dataclasses import replace
from datetime import date
from difflib import get_close_matches
from typing import Protocol

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.interpretation.models import FinanceQueryInterpretation
from minx_mcp.core.interpretation.runner import run_interpretation
from minx_mcp.core.models import (
    FinanceQueryClarificationType,
    FinanceQueryFilters,
    FinanceQueryIntent,
    FinanceQueryPlan,
)


class FinanceQueryReadProtocol(Protocol):
    def list_transaction_category_names(self) -> list[str]: ...
    def list_spending_merchant_names(self) -> list[str]: ...
    def list_account_names(self) -> list[str]: ...


async def interpret_finance_query(
    *,
    message: str,
    review_date: str,
    finance_api: FinanceQueryReadProtocol,
    llm: object,
) -> FinanceQueryPlan:
    _validate_iso_date(review_date, field_name="review_date")
    prompt = _render_finance_query_prompt(message, review_date, finance_api)
    raw = await run_interpretation(
        llm=llm,
        prompt=prompt,
        result_model=FinanceQueryInterpretation,
    )

    if raw.needs_clarification:
        return FinanceQueryPlan(
            intent=raw.intent,
            filters=_to_filters(raw),
            confidence=raw.confidence,
            needs_clarification=True,
            clarification_type=raw.clarification_type,
            question=raw.question,
            options=raw.options,
        )

    filters = _to_filters(raw)
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
            options=_suggest_options(filters.category_name, finance_api.list_transaction_category_names()),
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
            f"Message: {message}",
            f"Review date: {review_date}",
            "Known categories: " + ", ".join(finance_api.list_transaction_category_names()),
            "Known merchants: " + ", ".join(finance_api.list_spending_merchant_names()),
            "Known accounts: " + ", ".join(finance_api.list_account_names()),
        ]
    )


def _to_filters(raw: FinanceQueryInterpretation) -> FinanceQueryFilters:
    return FinanceQueryFilters(
        start_date=raw.filters.start_date,
        end_date=raw.filters.end_date,
        category_name=raw.filters.category_name,
        merchant=raw.filters.merchant,
        account_name=raw.filters.account_name,
        description_contains=raw.filters.description_contains,
    )


def _canonicalize_value(value: str | None, candidates: list[str]) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip().casefold()
    for candidate in candidates:
        if candidate.casefold() == normalized_value:
            return candidate
    return None


def _suggest_options(value: str, candidates: list[str]) -> list[str] | None:
    normalized = value.strip().casefold()
    substring_matches = [candidate for candidate in candidates if normalized in candidate.casefold()]
    if substring_matches:
        return substring_matches[:5]
    matches = get_close_matches(value, candidates, n=5, cutoff=0.4)
    return matches or None


def _validate_iso_date(value: str, *, field_name: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInputError(f"{field_name} must be a valid ISO date") from exc


def _clarify(
    *,
    intent: FinanceQueryIntent,
    filters: FinanceQueryFilters,
    confidence: float,
    clarification_type: FinanceQueryClarificationType,
    question: str,
    options: list[str] | None = None,
) -> FinanceQueryPlan:
    return FinanceQueryPlan(
        intent=intent,
        filters=filters,
        confidence=confidence,
        needs_clarification=True,
        clarification_type=clarification_type,
        question=question,
        options=options,
    )
