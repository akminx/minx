from __future__ import annotations

from minx_mcp.core.interpretation.context import (
    build_finance_query_context,
    build_goal_capture_context,
)
from minx_mcp.core.interpretation.logging import log_interpretation_failure


def test_log_interpretation_failure_summary_appears_in_logs(caplog):
    """Only the structured summary appears — not any raw user message text."""
    import logging

    with caplog.at_level(logging.WARNING, logger="minx_mcp.core.interpretation.logging"):
        log_interpretation_failure(
            task="finance_query",
            prompt_summary="message_len=47 merchants=1",
            error=RuntimeError("schema failure"),
        )
    assert "message_len=47 merchants=1" in caplog.text
    assert "schema failure" in caplog.text


def test_log_interpretation_failure_does_not_filter_caller_provided_error_content(caplog):
    """The logging helper logs what it receives — callers must pass safe content.
    This test verifies a safe caller gets safe log output."""
    import logging

    raw_user_text = "show me everything at Whole Foods last month"
    with caplog.at_level(logging.WARNING, logger="minx_mcp.core.interpretation.logging"):
        log_interpretation_failure(
            task="finance_query",
            prompt_summary=f"message_len={len(raw_user_text)} merchants=1",
            error=RuntimeError("schema failure"),  # technical error, not user text
        )
    # The raw user message should not appear because we passed a safe summary and safe error
    assert raw_user_text not in caplog.text
    # The safe summary and error type should appear
    assert f"message_len={len(raw_user_text)}" in caplog.text
    assert "schema failure" in caplog.text


def test_build_finance_query_context_caps_merchant_list():
    context = build_finance_query_context(
        message="test",
        review_date="2026-03-31",
        category_names=["Groceries"],
        merchant_names=[f"Merchant {i}" for i in range(150)],
        account_names=["DCU"],
    )
    assert len(context["merchant_names"]) == 100


def test_build_finance_query_context_caps_category_names():
    context = build_finance_query_context(
        message="test",
        review_date="2026-03-31",
        category_names=[f"Cat {i}" for i in range(120)],
        merchant_names=["Amazon"],
        account_names=["DCU"],
    )
    assert len(context["category_names"]) == 100


def test_build_finance_query_context_caps_account_names():
    context = build_finance_query_context(
        message="test",
        review_date="2026-03-31",
        category_names=["Groceries"],
        merchant_names=["Amazon"],
        account_names=[f"Account {i}" for i in range(30)],
    )
    assert len(context["account_names"]) == 20


def test_build_goal_capture_context_caps_category_and_merchant_lists():
    context = build_goal_capture_context(
        message="test",
        review_date="2026-03-15",
        active_goals=[],
        category_names=[f"Cat {i}" for i in range(80)],
        merchant_names=[f"Merchant {i}" for i in range(80)],
    )
    assert len(context["category_names"]) == 50
    assert len(context["merchant_names"]) == 50


def test_build_goal_capture_context_caps_active_goals():
    class _FakeGoal:
        def __init__(self, i: int) -> None:
            self.id = i
            self.title = f"Goal {i}"
            self.status = "active"
            self.period = "monthly"
            self.target_value = 10000

    context = build_goal_capture_context(
        message="test",
        review_date="2026-03-15",
        active_goals=[_FakeGoal(i) for i in range(15)],
        category_names=["Groceries"],
        merchant_names=["Amazon"],
    )
    assert len(context["active_goals"]) == 10
