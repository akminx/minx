from __future__ import annotations

import asyncio
import inspect

import pytest

from minx_mcp.core.interpretation.finance_query import (
    interpret_finance_query as _interpret_finance_query,
)


class _StubFinanceQueryRead:
    def list_transaction_category_names(self) -> list[str]:
        return ["Groceries", "Restaurants", "Uncategorized"]

    def list_spending_merchant_names(self) -> list[str]:
        return ["Target", "Whole Foods"]

    def list_account_names(self) -> list[str]:
        return ["DCU", "Discover"]


class _StubFinanceQueryLLM:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def run_json_prompt(self, prompt: str) -> str:
        assert "Whole Foods" in prompt
        return self.payload


def interpret_finance_query(**kwargs):
    result = _interpret_finance_query(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def test_finance_query_interpretation_resolves_sum_spending_request() -> None:
    plan = interpret_finance_query(
        message="how much did I spend on restaurants this week",
        review_date="2026-03-15",
        finance_api=_StubFinanceQueryRead(),
        llm=_StubFinanceQueryLLM(
            '{"intent":"sum_spending","filters":{"start_date":"2026-03-09",'
            '"end_date":"2026-03-15","category_name":"Restaurants"},'
            '"confidence":0.93,"needs_clarification":false}'
        ),
    )

    assert plan.intent == "sum_spending"
    assert plan.filters.category_name == "Restaurants"
    assert plan.filters.start_date == "2026-03-09"
    assert plan.filters.end_date == "2026-03-15"
    assert plan.needs_clarification is False


@pytest.mark.asyncio
async def test_finance_query_interpretation_is_async_safe_inside_running_loop() -> None:
    plan = await _interpret_finance_query(
        message="show me everything at Whole Foods last month",
        review_date="2026-03-31",
        finance_api=_StubFinanceQueryRead(),
        llm=_StubFinanceQueryLLM(
            '{"intent":"list_transactions","filters":{"start_date":"2026-03-01",'
            '"end_date":"2026-03-31","merchant":"Whole Foods"},'
            '"confidence":0.94,"needs_clarification":false}'
        ),
    )

    assert plan.intent == "list_transactions"
    assert plan.filters.merchant == "Whole Foods"
    assert plan.needs_clarification is False


@pytest.mark.asyncio
async def test_finance_query_interpretation_canonicalizes_merchant_without_punctuation() -> None:
    class ReadWithCanonicalMerchant(_StubFinanceQueryRead):
        def list_spending_merchant_names(self) -> list[str]:
            return ["Joe's Cafe"]

    class MerchantLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            assert "Joe's Cafe" in prompt
            return (
                '{"intent":"list_transactions","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31","merchant":"joes cafe"},'
                '"confidence":0.94,"needs_clarification":false}'
            )

    plan = await _interpret_finance_query(
        message="show me everything at joes cafe last month",
        review_date="2026-03-31",
        finance_api=ReadWithCanonicalMerchant(),
        llm=MerchantLLM(),
    )

    assert plan.needs_clarification is False
    assert plan.filters.merchant == "Joe's Cafe"


@pytest.mark.asyncio
async def test_finance_query_interpretation_canonicalizes_statement_style_merchant_name() -> None:
    class ReadWithCanonicalMerchant(_StubFinanceQueryRead):
        def list_spending_merchant_names(self) -> list[str]:
            return ["Joe's Cafe"]

    class MerchantLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            assert "Joe's Cafe" in prompt
            return (
                '{"intent":"list_transactions","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31","merchant":"SQ *JOES CAFE 1234"},'
                '"confidence":0.94,"needs_clarification":false}'
            )

    plan = await _interpret_finance_query(
        message="show me everything at SQ *JOES CAFE 1234 last month",
        review_date="2026-03-31",
        finance_api=ReadWithCanonicalMerchant(),
        llm=MerchantLLM(),
    )

    assert plan.needs_clarification is False
    assert plan.filters.merchant == "Joe's Cafe"


def test_finance_query_interpretation_returns_clarify_plan_for_ambiguous_merchant() -> None:
    plan = interpret_finance_query(
        message="show me everything at target last month",
        review_date="2026-03-31",
        finance_api=_StubFinanceQueryRead(),
        llm=_StubFinanceQueryLLM(
            '{"intent":"list_transactions","filters":{},"confidence":0.51,'
            '"needs_clarification":true,"clarification_type":"ambiguous_merchant",'
            '"question":"Which merchant do you mean?","options":["Target","Target Optical"]}'
        ),
    )

    assert plan.intent == "list_transactions"
    assert plan.needs_clarification is True
    assert plan.clarification_type == "ambiguous_merchant"
    assert plan.clarification_template == "finance_query.clarify.ambiguous_merchant"
    assert plan.clarification_slots == {
        "intent": "list_transactions",
        "filters": {"start_date": "2026-02-01", "end_date": "2026-02-28"},
        "field": "merchant",
    }
    assert plan.options == ["Target"]


def test_finance_query_interpretation_model_rejects_clarify_without_type() -> None:
    from pydantic import ValidationError

    from minx_mcp.core.interpretation.models import FinanceQueryInterpretation

    with pytest.raises(ValidationError):
        FinanceQueryInterpretation(
            intent="list_transactions",
            confidence=0.5,
            needs_clarification=True,
            clarification_type=None,
            question="What did you mean?",
        )


def test_finance_query_interpretation_model_rejects_clarify_without_question() -> None:
    from pydantic import ValidationError

    from minx_mcp.core.interpretation.models import FinanceQueryInterpretation

    with pytest.raises(ValidationError):
        FinanceQueryInterpretation(
            intent="list_transactions",
            confidence=0.5,
            needs_clarification=True,
            clarification_type="ambiguous_merchant",
            question=None,
        )


def test_finance_query_interpretation_fills_this_week_dates_when_llm_omits_them() -> None:
    plan = interpret_finance_query(
        message="how much did I spend on restaurants this week",
        review_date="2026-03-15",
        finance_api=_StubFinanceQueryRead(),
        llm=_StubFinanceQueryLLM(
            '{"intent":"sum_spending","filters":{"category_name":"Restaurants"},'
            '"confidence":0.93,"needs_clarification":false}'
        ),
    )

    assert plan.intent == "sum_spending"
    assert plan.needs_clarification is False
    assert plan.filters.start_date == "2026-03-09"
    assert plan.filters.end_date == "2026-03-15"


def test_finance_query_interpretation_fills_last_month_dates_when_llm_omits_them() -> None:
    plan = interpret_finance_query(
        message="show me everything at Whole Foods last month",
        review_date="2026-03-31",
        finance_api=_StubFinanceQueryRead(),
        llm=_StubFinanceQueryLLM(
            '{"intent":"list_transactions","filters":{"merchant":"Whole Foods"},'
            '"confidence":0.94,"needs_clarification":false}'
        ),
    )

    assert plan.intent == "list_transactions"
    assert plan.needs_clarification is False
    assert plan.filters.start_date == "2026-02-01"
    assert plan.filters.end_date == "2026-02-28"


def test_finance_query_interpretation_preserves_recoverable_dates_on_clarify() -> None:
    plan = interpret_finance_query(
        message="how much did I spend at Whole Fuds this week",
        review_date="2026-03-15",
        finance_api=_StubFinanceQueryRead(),
        llm=_StubFinanceQueryLLM(
            '{"intent":"sum_spending","filters":{"merchant":"Whole Fuds"},'
            '"confidence":0.61,"needs_clarification":true,'
            '"clarification_type":"unknown_merchant",'
            '"question":"Which merchant did you mean?","options":["Whole Foods"]}'
        ),
    )

    assert plan.needs_clarification is True
    assert plan.filters.start_date == "2026-03-09"
    assert plan.filters.end_date == "2026-03-15"
