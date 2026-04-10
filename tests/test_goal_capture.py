from __future__ import annotations

import asyncio
import inspect

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.goal_capture import (
    _render_goal_capture_prompt,
    capture_goal_message as _capture_goal_message,
)
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from minx_mcp.core.models import GoalCaptureOption, GoalCaptureResult, GoalRecord


class _StubFinanceRead:
    def list_goal_category_names(self) -> list[str]:
        return ["Cafe", "Dining Out", "Groceries"]

    def list_spending_merchant_names(self) -> list[str]:
        return ["Amazon", "Cafe", "Netflix"]


class _AmbiguousSpellingsFinanceRead:
    def list_goal_category_names(self) -> list[str]:
        return ["M&M"]

    def list_spending_merchant_names(self) -> list[str]:
        return ["M M"]


class _CanonicalMerchantFinanceRead:
    def list_goal_category_names(self) -> list[str]:
        return ["Dining Out"]

    def list_spending_merchant_names(self) -> list[str]:
        return ["Joe's Cafe"]


class _StubGoalCaptureLLM:
    def __init__(self, payload: str, expected_substring: str | None = "Amazon") -> None:
        self.payload = payload
        self.expected_substring = expected_substring

    async def run_json_prompt(self, prompt: str) -> str:
        if self.expected_substring is not None:
            assert self.expected_substring in prompt
        return self.payload


class _TestConfig:
    def __init__(self, db_path, vault_path) -> None:
        self.db_path = db_path
        self.vault_path = vault_path


def capture_goal_message(**kwargs):
    result = _capture_goal_message(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _goal_record(**overrides: object) -> GoalRecord:
    defaults = dict(
        id=7,
        goal_type="spending_cap",
        title="Dining Out Spending Cap",
        status="active",
        metric_type="sum_below",
        target_value=25_000,
        period="monthly",
        domain="finance",
        category_names=["Dining Out"],
        merchant_names=[],
        account_names=[],
        starts_on="2026-03-01",
        ends_on=None,
        notes=None,
        created_at="2026-03-01 00:00:00",
        updated_at="2026-03-01 00:00:00",
    )
    defaults.update(overrides)
    return GoalRecord(**defaults)


def test_capture_goal_message_builds_create_payload_for_category_goal_with_starts_on() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $250 on dining out this month",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "create"
    assert result.action == "goal_create"
    assert result.payload == {
        "goal_type": "spending_cap",
        "title": "Dining Out Spending Cap",
        "metric_type": "sum_below",
        "target_value": 25_000,
        "period": "monthly",
        "domain": "finance",
        "category_names": ["Dining Out"],
        "merchant_names": [],
        "account_names": [],
        "starts_on": "2026-03-01",
        "ends_on": None,
        "notes": None,
    }


def test_capture_goal_message_builds_daily_create_payload_for_today_phrase() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $25 on dining out today",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "create"
    assert result.action == "goal_create"
    assert result.payload is not None
    assert result.payload["period"] == "daily"
    assert result.payload["starts_on"] == "2026-03-15"


def test_capture_goal_message_canonicalizes_statement_style_merchant_subject() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $25 at SQ *JOES CAFE 1234 this month",
        review_date="2026-03-15",
        finance_api=_CanonicalMerchantFinanceRead(),
        goals=[],
    )

    assert result.result_type == "create"
    assert result.payload == {
        "goal_type": "spending_cap",
        "title": "Joe's Cafe Spending Cap",
        "metric_type": "sum_below",
        "target_value": 2_500,
        "period": "monthly",
        "domain": "finance",
        "category_names": [],
        "merchant_names": ["Joe's Cafe"],
        "account_names": [],
        "starts_on": "2026-03-01",
        "ends_on": None,
        "notes": None,
    }


def test_capture_goal_message_honors_explicit_iso_start_date() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $25 on dining out starting 2026-04-10",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "create"
    assert result.payload is not None
    assert result.payload["starts_on"] == "2026-04-10"


def test_capture_goal_message_defaults_starts_on_to_review_date_without_relative_period() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $25 on dining out",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "create"
    assert result.payload is not None
    assert result.payload["starts_on"] == "2026-03-15"


def test_capture_goal_message_returns_vague_intent_clarify_for_unresolved_create_subject() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $25 on mystery stuff this month",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "vague_intent"
    assert result.question is not None
    assert result.action is None
    assert result.resume_payload is None


def test_capture_goal_message_rejects_invalid_explicit_start_date() -> None:
    with pytest.raises(InvalidInputError, match="start date"):
        capture_goal_message(
            message="Make a goal to spend less than $25 on dining out starting 2026-04-99",
            review_date="2026-03-15",
            finance_api=_StubFinanceRead(),
            goals=[],
        )


def test_capture_goal_message_returns_missing_target_clarify_for_goal_like_create_message() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less on dining out this month",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "missing_target"
    assert result.action == "goal_create"
    assert result.question is not None


def test_capture_goal_message_returns_ambiguous_subject_clarify_for_category_vs_merchant_collision() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $60 at Cafe this week",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "ambiguous_subject"
    assert result.action == "goal_create"
    assert result.resume_payload == {
        "goal_type": "spending_cap",
        "title": "Cafe Spending Cap",
        "metric_type": "sum_below",
        "target_value": 6_000,
        "period": "weekly",
        "domain": "finance",
        "category_names": [],
        "merchant_names": [],
        "account_names": [],
        "starts_on": "2026-03-09",
        "ends_on": None,
        "notes": None,
    }
    assert len(result.options or []) == 2


def test_capture_goal_message_preserves_distinct_merchant_spelling_in_ambiguous_create_subject() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $60 at M&M this week",
        review_date="2026-03-15",
        finance_api=_AmbiguousSpellingsFinanceRead(),
        goals=[],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "ambiguous_subject"
    assert result.resume_payload == {
        "goal_type": "spending_cap",
        "title": "M&M Spending Cap",
        "metric_type": "sum_below",
        "target_value": 6_000,
        "period": "weekly",
        "domain": "finance",
        "category_names": [],
        "merchant_names": [],
        "account_names": [],
        "starts_on": "2026-03-09",
        "ends_on": None,
        "notes": None,
    }
    assert result.options is not None
    assert result.options[0].payload_fragment == {
        "title": "M&M Spending Cap",
        "category_names": ["M&M"],
    }
    assert result.options[1].payload_fragment == {
        "title": "M M Spending Cap",
        "merchant_names": ["M M"],
    }


def test_capture_goal_message_accepts_valid_non_20xx_explicit_start_dates() -> None:
    older = capture_goal_message(
        message="Make a goal to spend less than $25 on dining out starting 1999-04-10",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )
    future = capture_goal_message(
        message="Make a goal to spend less than $25 on dining out starting 2100-04-10",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert older.result_type == "create"
    assert older.payload is not None
    assert older.payload["starts_on"] == "1999-04-10"
    assert future.result_type == "create"
    assert future.payload is not None
    assert future.payload["starts_on"] == "2100-04-10"


def test_capture_goal_message_returns_ambiguous_goal_clarify_with_resume_payload() -> None:
    result = capture_goal_message(
        message="Pause my dining out goal",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[
            _goal_record(id=7, title="Dining Out Spending Cap", period="monthly"),
            _goal_record(id=8, title="Dining Out Vacation Spending Cap", period="weekly"),
        ],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "ambiguous_goal"
    assert result.action == "goal_update"
    assert result.resume_payload == {"status": "paused"}
    assert result.options is not None
    assert result.options[0].filter_summary == "category_names=['Dining Out']"
    assert result.options[1].filter_summary == "category_names=['Dining Out']"


def test_capture_goal_message_builds_update_payload_for_pause() -> None:
    result = capture_goal_message(
        message="Pause my dining out goal",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[_goal_record()],
    )

    assert result.result_type == "update"
    assert result.goal_id == 7
    assert result.action == "goal_update"
    assert result.assistant_message is not None
    assert result.payload == {"status": "paused"}


def test_capture_goal_message_builds_create_assistant_message() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $250 on dining out this month",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "create"
    assert result.assistant_message is not None


def test_capture_goal_message_matches_statement_style_merchant_for_goal_update() -> None:
    result = capture_goal_message(
        message="Pause my SQ *JOES CAFE 1234 goal",
        review_date="2026-03-15",
        finance_api=_CanonicalMerchantFinanceRead(),
        goals=[
            _goal_record(
                id=9,
                title="Joe's Cafe Spending Cap",
                category_names=[],
                merchant_names=["Joe's Cafe"],
                account_names=[],
            )
        ],
    )

    assert result.result_type == "update"
    assert result.goal_id == 9
    assert result.payload == {"status": "paused"}


def test_capture_goal_message_treats_unpause_as_resume_not_pause() -> None:
    result = capture_goal_message(
        message="Unpause my dining out goal",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[_goal_record(status="paused")],
    )

    assert result.result_type == "update"
    assert result.goal_id == 7
    assert result.payload == {"status": "active"}


def test_capture_goal_message_returns_missing_goal_for_supported_update_without_target() -> None:
    result = capture_goal_message(
        message="Pause my dining out goal",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "missing_goal"
    assert result.action == "goal_update"


def test_capture_goal_message_returns_missing_target_for_retarget_without_amount() -> None:
    result = capture_goal_message(
        message="Change my dining out goal",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[_goal_record()],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "missing_target"
    assert result.action == "goal_update"
    assert result.question is not None


def test_capture_goal_message_returns_missing_target_before_ambiguous_goal_without_resume_update() -> None:
    result = capture_goal_message(
        message="Change my dining out goal",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[
            _goal_record(id=7, title="Dining Out Spending Cap", period="monthly"),
            _goal_record(id=8, title="Dining Out Vacation Spending Cap", period="weekly"),
        ],
    )

    assert result.result_type == "clarify"
    assert result.clarification_type == "missing_target"
    assert result.question == "What should the new target be?"
    assert result.action is None
    assert result.resume_payload is None
    assert result.options is None


def test_capture_goal_message_returns_no_match_for_unsupported_goal_family() -> None:
    result = capture_goal_message(
        message="Change my walk 10k steps goal to $400",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[
            _goal_record(
                id=3,
                goal_type="habit",
                title="Walk 10k steps",
                status="active",
                metric_type="count_above",
                target_value=10,
                period="daily",
                category_names=[],
                merchant_names=[],
                account_names=[],
            )
        ],
    )

    assert result.result_type == "no_match"


def test_capture_goal_message_includes_active_goals_in_llm_prompt() -> None:
    result = capture_goal_message(
        message="Pause my dining out goal",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[_goal_record()],
        llm=_StubGoalCaptureLLM(
            '{"intent":"invalid"}',
            expected_substring="Dining Out Spending Cap",
        ),
    )

    assert result.result_type == "update"
    assert result.goal_id == 7
    assert result.payload == {"status": "paused"}


def test_render_goal_capture_prompt_labels_candidate_goals_not_active_goals() -> None:
    prompt = _render_goal_capture_prompt(
        "Pause my dining out goal",
        "2026-03-15",
        _StubFinanceRead(),
        [_goal_record(), _goal_record(id=8, status="paused")],
    )

    assert "Candidate goals:" in prompt
    assert "Active goals:" not in prompt


def test_capture_goal_message_uses_llm_for_natural_language_create_resolution() -> None:
    result = capture_goal_message(
        message="I want to track my Amazon spending under $200 monthly",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
        llm=_StubGoalCaptureLLM(
            '{"intent":"create","confidence":0.97,"subject_kind":"merchant","subject":"Amazon","period":"monthly","target_value":20000}'
        ),
    )

    assert result.result_type == "create"
    assert result.payload is not None
    assert result.payload["merchant_names"] == ["Amazon"]
    assert result.payload["target_value"] == 20_000
    assert result.payload["period"] == "monthly"


def test_capture_goal_message_uses_llm_update_intent_when_deterministic_parser_misses() -> None:
    result = capture_goal_message(
        message="put my dining goal on hold",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[_goal_record()],
        llm=_StubGoalCaptureLLM(
            '{"intent":"update","confidence":0.99,"update_kind":"pause","goal_id":7}',
            expected_substring="Dining Out Spending Cap",
        ),
    )

    assert result.result_type == "update"
    assert result.goal_id == 7
    assert result.payload == {"status": "paused"}


@pytest.mark.asyncio
async def test_capture_goal_message_is_async_safe_inside_running_loop() -> None:
    result = await _capture_goal_message(
        message="I want to track my Amazon spending under $200 monthly",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
        llm=_StubGoalCaptureLLM(
            '{"intent":"create","confidence":0.97,"subject_kind":"merchant","subject":"Amazon","period":"monthly","target_value":20000}'
        ),
    )

    assert result.result_type == "create"
    assert result.payload is not None
    assert result.payload["merchant_names"] == ["Amazon"]


@pytest.mark.asyncio
async def test_goal_capture_tool_is_async_safe_inside_running_loop(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    server = create_core_server(_TestConfig(db_path, tmp_path / "vault"))
    goal_capture = server._tool_manager.get_tool("goal_capture").fn

    result = await goal_capture(
        message="Make a goal to spend less than $250 on dining out this month",
        review_date="2026-03-15",
    )

    assert result["success"] is True
    assert result["data"]["result_type"] == "create"
    assert result["data"]["payload"]["starts_on"] == "2026-03-01"


def test_capture_goal_message_falls_back_when_llm_output_is_invalid() -> None:
    result = capture_goal_message(
        message="I want to track my Amazon spending under $200 monthly",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
        llm=_StubGoalCaptureLLM('{"intent":"invalid"}'),
    )

    assert result.result_type == "no_match"


def test_capture_goal_message_falls_back_to_deterministic_create_when_llm_payload_is_malformed() -> None:
    result = capture_goal_message(
        message="Make a goal to spend less than $250 on dining out this month",
        review_date="2026-03-15",
        finance_api=_StubFinanceRead(),
        goals=[],
        llm=_StubGoalCaptureLLM("not-json"),
    )

    assert result.result_type == "create"
    assert result.payload is not None
    assert result.payload["title"] == "Dining Out Spending Cap"
    assert result.payload["category_names"] == ["Dining Out"]
    assert result.payload["starts_on"] == "2026-03-01"


def test_goal_capture_result_rejects_clarify_without_clarification_type() -> None:
    with pytest.raises(ValueError, match="clarification_type"):
        GoalCaptureResult(result_type="clarify", action="goal_update")


def test_goal_capture_result_allows_clarify_without_action_or_resume_payload() -> None:
    result = GoalCaptureResult(
        result_type="clarify",
        clarification_type="vague_intent",
        question="What spending category or merchant should this goal track?",
    )

    assert result.action is None
    assert result.resume_payload is None


def test_goal_capture_result_rejects_no_match_without_assistant_message() -> None:
    with pytest.raises(ValueError, match="assistant_message"):
        GoalCaptureResult(result_type="no_match")


def test_goal_capture_option_rejects_category_option_with_merchant_name() -> None:
    with pytest.raises(ValueError, match="category_name"):
        GoalCaptureOption(
            kind="category",
            merchant_name="Cafe",
            payload_fragment={"category_names": ["Cafe"]},
        )


def test_goal_capture_option_rejects_category_option_without_label() -> None:
    with pytest.raises(ValueError, match="label"):
        GoalCaptureOption(
            kind="category",
            category_name="Cafe",
            payload_fragment={"category_names": ["Cafe"]},
        )


def test_goal_capture_option_rejects_category_option_without_payload_fragment() -> None:
    with pytest.raises(ValueError, match="payload_fragment"):
        GoalCaptureOption(kind="category", category_name="Cafe", label="Cafe")


def test_goal_capture_option_rejects_category_option_with_inconsistent_label() -> None:
    with pytest.raises(ValueError, match="label"):
        GoalCaptureOption(
            kind="category",
            category_name="Cafe",
            label="Coffee",
            payload_fragment={"category_names": ["Cafe"]},
        )


def test_goal_capture_option_rejects_category_option_with_wrong_payload_key() -> None:
    with pytest.raises(ValueError, match="payload_fragment"):
        GoalCaptureOption(
            kind="category",
            category_name="Cafe",
            label="Cafe",
            payload_fragment={"merchant_names": ["Cafe"]},
        )


def test_goal_capture_option_rejects_category_option_with_extra_filter_key() -> None:
    with pytest.raises(ValueError, match="payload_fragment"):
        GoalCaptureOption(
            kind="category",
            category_name="Cafe",
            label="Cafe",
            payload_fragment={
                "category_names": ["Cafe"],
                "merchant_names": ["Cafe"],
            },
        )


def test_goal_capture_option_rejects_goal_option_without_label() -> None:
    with pytest.raises(ValueError, match="label"):
        GoalCaptureOption(kind="goal", goal_id=7, title="Dining Out Spending Cap")


def test_goal_capture_option_rejects_missing_or_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        GoalCaptureOption(label="Cafe", payload_fragment={"category_names": ["Cafe"]})

    with pytest.raises(ValueError, match="kind"):
        GoalCaptureOption(  # type: ignore[arg-type]
            kind="bogus",
            label="Cafe",
            payload_fragment={"category_names": ["Cafe"]},
        )
