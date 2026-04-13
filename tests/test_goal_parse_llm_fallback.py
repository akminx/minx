from __future__ import annotations

import logging

import pytest

from minx_mcp.core.goal_parse import capture_goal_message


class _FailingLLM:
    async def run_structured_prompt(self, prompt, result_model):
        raise RuntimeError("LLM unavailable")

    async def run_json_prompt(self, prompt):
        raise RuntimeError("LLM unavailable")


class _StubFinanceRead:
    def get_spending_summary(self, start_date: str, end_date: str):
        return {}

    def get_uncategorized(self, start_date: str, end_date: str):
        return []

    def get_import_job_issues(self):
        return []

    def list_account_names(self) -> list[str]:
        return []

    def get_period_comparison(self, current_start, current_end, prior_start, prior_end):
        return {}

    def list_goal_category_names(self) -> list[str]:
        return ["Dining Out"]

    def list_spending_merchant_names(self) -> list[str]:
        return []

    def get_filtered_spending_total(
        self, start_date, end_date, *, category_names=None, merchant_names=None, account_names=None
    ) -> int:
        return 0

    def get_filtered_transaction_count(
        self, start_date, end_date, *, category_names=None, merchant_names=None, account_names=None
    ) -> int:
        return 0


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_no_match() -> None:
    result = await capture_goal_message(
        message="what's for lunch?",
        review_date="2026-04-12",
        finance_api=_StubFinanceRead(),
        goals=[],
        llm=_FailingLLM(),
    )

    assert result.result_type == "no_match"


@pytest.mark.asyncio
async def test_llm_exception_logs_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="minx_mcp.core.goal_parse"):
        await capture_goal_message(
            message="what's for lunch?",
            review_date="2026-04-12",
            finance_api=_StubFinanceRead(),
            goals=[],
            llm=_FailingLLM(),
        )

    assert any(
        "LLM goal capture failed" in r.message and "RuntimeError" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_llm_none_falls_back_to_deterministic_regex() -> None:
    result = await capture_goal_message(
        message="spend less than $100 on Dining Out monthly",
        review_date="2026-04-12",
        finance_api=_StubFinanceRead(),
        goals=[],
        llm=None,
    )

    assert result.result_type == "create"
