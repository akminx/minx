from __future__ import annotations

import asyncio
import inspect

import pytest

from minx_mcp.core.interpretation.finance_query import interpret_finance_query as _interpret_finance_query


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
            (
                '{"intent":"sum_spending","filters":{"start_date":"2026-03-09",'
                '"end_date":"2026-03-15","category_name":"Restaurants"},'
                '"confidence":0.93,"needs_clarification":false}'
            )
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
            (
                '{"intent":"list_transactions","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31","merchant":"Whole Foods"},'
                '"confidence":0.94,"needs_clarification":false}'
            )
        ),
    )

    assert plan.intent == "list_transactions"
    assert plan.filters.merchant == "Whole Foods"
    assert plan.needs_clarification is False


def test_finance_query_interpretation_returns_clarify_plan_for_ambiguous_merchant() -> None:
    plan = interpret_finance_query(
        message="show me everything at target last month",
        review_date="2026-03-31",
        finance_api=_StubFinanceQueryRead(),
        llm=_StubFinanceQueryLLM(
            (
                '{"intent":"list_transactions","filters":{},"confidence":0.51,'
                '"needs_clarification":true,"clarification_type":"ambiguous_merchant",'
                '"question":"Which merchant do you mean?","options":["Target","Target Optical"]}'
            )
        ),
    )

    assert plan.intent == "list_transactions"
    assert plan.needs_clarification is True
    assert plan.clarification_type == "ambiguous_merchant"
    assert plan.options == ["Target", "Target Optical"]
