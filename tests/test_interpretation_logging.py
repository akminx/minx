from __future__ import annotations

import pytest

from minx_mcp.core.interpretation.context import (
    build_finance_query_context,
    build_goal_capture_context,
)
from minx_mcp.core.interpretation.logging import log_interpretation_failure


def test_log_interpretation_failure_redacts_full_user_message(caplog):
    log_interpretation_failure(
        task="finance_query",
        prompt_summary="message_len=47 merchants=1 accounts=1",
        error=RuntimeError("schema failure"),
    )
    assert "schema failure" in caplog.text
    assert "show me everything" not in caplog.text  # full message must not appear


def test_build_finance_query_context_caps_merchant_list():
    context = build_finance_query_context(
        message="test", review_date="2026-03-31",
        category_names=["Groceries"],
        merchant_names=[f"Merchant {i}" for i in range(150)],
        account_names=["DCU"],
    )
    assert len(context["merchant_names"]) == 100


def test_build_goal_capture_context_caps_category_and_merchant_lists():
    context = build_goal_capture_context(
        message="test", review_date="2026-03-15",
        active_goals=[],
        category_names=[f"Cat {i}" for i in range(80)],
        merchant_names=[f"Merchant {i}" for i in range(80)],
    )
    assert len(context["category_names"]) == 50
    assert len(context["merchant_names"]) == 50
