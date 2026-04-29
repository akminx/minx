"""Goal-related MCP tools: create/list/get/update/archive/parse/trajectory.

The public entry point is :func:`register_goal_tools`. Everything else
is private to this module. ``parse_goal_input`` is imported at module
scope so tests can patch ``minx_mcp.core.tools.goals.parse_goal_input``
(the previous patch target ``minx_mcp.core.server.parse_goal_input``
has been updated in the test suite).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import TypeVar, cast

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import (
    InvalidInputError,
    ToolResponse,
    wrap_async_tool_call,
    wrap_tool_call,
)
from minx_mcp.core.goal_parse import parse_goal_input
from minx_mcp.core.goal_progress import build_progress_for_goal
from minx_mcp.core.goals import GoalService
from minx_mcp.core.llm import create_llm
from minx_mcp.core.models import (
    FinanceReadInterface,
    GoalCaptureOption,
    GoalCaptureResult,
    GoalCreateInput,
    GoalProgress,
    GoalRecord,
    GoalUpdateInput,
    JSONLLMInterface,
)
from minx_mcp.core.tools._shared import CoreServiceConfig, resolve_review_date
from minx_mcp.core.trajectory import get_goal_trajectory
from minx_mcp.db import scoped_connection
from minx_mcp.finance.read_api import FinanceReadAPI
from minx_mcp.finance.read_models import (
    ImportJobIssue,
    IncomeSummary,
    PeriodComparison,
    SpendingSummary,
    UncategorizedSummary,
)

__all__ = ["parse_goal_input", "register_goal_tools"]

_T = TypeVar("_T")


def register_goal_tools(mcp: FastMCP, config: CoreServiceConfig) -> None:
    @mcp.tool(name="goal_create")
    def goal_create(
        title: str,
        goal_type: str,
        metric_type: str,
        target_value: int,
        period: str,
        domain: str = "finance",
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
        starts_on: str | None = None,
        ends_on: str | None = None,
        notes: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _goal_create(
                config,
                GoalCreateInput(
                    title=title,
                    goal_type=goal_type,
                    metric_type=metric_type,
                    target_value=target_value,
                    period=period,
                    domain=domain,
                    category_names=category_names or [],
                    merchant_names=merchant_names or [],
                    account_names=account_names or [],
                    starts_on=starts_on if starts_on is not None else date.today().isoformat(),
                    ends_on=ends_on,
                    notes=notes,
                ),
            ),
            tool_name="goal_create",
        )

    @mcp.tool(name="goal_list")
    def goal_list(status: str | None = None) -> ToolResponse:
        return wrap_tool_call(
            lambda: _goal_list(config, status),
            tool_name="goal_list",
        )

    @mcp.tool(name="goal_get")
    def goal_get(goal_id: int, review_date: str | None = None) -> ToolResponse:
        return wrap_tool_call(
            lambda: _goal_get(config, goal_id, review_date),
            tool_name="goal_get",
        )

    @mcp.tool(name="goal_update")
    def goal_update(
        goal_id: int,
        title: str | None = None,
        target_value: int | None = None,
        status: str | None = None,
        ends_on: str | None = None,
        notes: str | None = None,
        clear_ends_on: bool = False,
        clear_notes: bool = False,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _goal_update(
                config,
                goal_id,
                GoalUpdateInput(
                    title=title,
                    target_value=target_value,
                    status=status,
                    ends_on=ends_on,
                    notes=notes,
                    clear_ends_on=clear_ends_on,
                    clear_notes=clear_notes,
                ),
            ),
            tool_name="goal_update",
        )

    @mcp.tool(name="goal_archive")
    def goal_archive(goal_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _goal_archive(config, goal_id),
            tool_name="goal_archive",
        )

    @mcp.tool(name="goal_parse")
    async def goal_parse(
        message: str | None = None,
        structured_input: dict[str, object] | None = None,
        review_date: str | None = None,
    ) -> ToolResponse:
        return await wrap_async_tool_call(
            lambda: _goal_parse(config, message, structured_input, review_date),
            tool_name="goal_parse",
        )

    @mcp.tool(name="get_goal_trajectory")
    def goal_trajectory(
        goal_id: int,
        periods: int = 4,
        as_of_date: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: get_goal_trajectory(
                config.db_path,
                goal_id=goal_id,
                periods=periods,
                as_of_date=as_of_date,
            ),
            tool_name="get_goal_trajectory",
        )


# ---------------------------------------------------------------------------
# Synchronous goal helpers
# ---------------------------------------------------------------------------


def _goal_create(config: CoreServiceConfig, payload: GoalCreateInput) -> dict[str, object]:
    with scoped_connection(config.db_path) as conn:
        goal = GoalService(conn).create_goal(payload)
        return {"goal": _goal_record_to_dict(goal)}


def _goal_list(config: CoreServiceConfig, status: str | None) -> dict[str, object]:
    with scoped_connection(config.db_path) as conn:
        goals = GoalService(conn).list_goals(status=status)
        return {"goals": [_goal_record_to_dict(goal) for goal in goals]}


def _goal_get(
    config: CoreServiceConfig,
    goal_id: int,
    review_date: str | None,
) -> dict[str, object]:
    effective_review_date = resolve_review_date(review_date)
    with scoped_connection(config.db_path) as conn:
        goal_service = GoalService(conn)
        goal = goal_service.get_goal(goal_id)
        progress = build_progress_for_goal(
            effective_review_date,
            goal,
            FinanceReadAPI(conn),
        )
        return {
            "goal": _goal_record_to_dict(goal),
            "progress": _goal_progress_to_dict(progress),
        }


def _goal_update(
    config: CoreServiceConfig,
    goal_id: int,
    payload: GoalUpdateInput,
) -> dict[str, object]:
    with scoped_connection(config.db_path) as conn:
        goal = GoalService(conn).update_goal(goal_id, payload)
        return {"goal": _goal_record_to_dict(goal)}


def _goal_archive(config: CoreServiceConfig, goal_id: int) -> dict[str, object]:
    with scoped_connection(config.db_path) as conn:
        goal = GoalService(conn).archive_goal(goal_id)
        return {"goal": _goal_record_to_dict(goal)}


# ---------------------------------------------------------------------------
# Async goal_parse + supporting scoped adapters
# ---------------------------------------------------------------------------


class _ScopingFinanceReadAPI:
    """FinanceReadInterface backed by short-lived connections (no handle across awaits)."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def _with_api(self, fn: Callable[[FinanceReadAPI], _T]) -> _T:
        with scoped_connection(self._db_path) as conn:
            return fn(FinanceReadAPI(conn))

    def get_spending_summary(self, start_date: str, end_date: str) -> SpendingSummary:
        return self._with_api(lambda api: api.get_spending_summary(start_date, end_date))

    def get_uncategorized(self, start_date: str, end_date: str) -> UncategorizedSummary:
        return self._with_api(lambda api: api.get_uncategorized(start_date, end_date))

    def get_import_job_issues(self) -> list[ImportJobIssue]:
        return self._with_api(lambda api: api.get_import_job_issues())

    def list_account_names(self) -> list[str]:
        return self._with_api(lambda api: api.list_account_names())

    def get_period_comparison(
        self,
        current_start: str,
        current_end: str,
        prior_start: str,
        prior_end: str,
    ) -> PeriodComparison:
        return self._with_api(
            lambda api: api.get_period_comparison(
                current_start, current_end, prior_start, prior_end
            )
        )

    def list_goal_category_names(self) -> list[str]:
        return self._with_api(lambda api: api.list_goal_category_names())

    def list_spending_merchant_names(self) -> list[str]:
        return self._with_api(lambda api: api.list_spending_merchant_names())

    def get_filtered_spending_total(
        self,
        start_date: str,
        end_date: str,
        *,
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
    ) -> int:
        return self._with_api(
            lambda api: api.get_filtered_spending_total(
                start_date,
                end_date,
                category_names=category_names,
                merchant_names=merchant_names,
                account_names=account_names,
            )
        )

    def get_filtered_transaction_count(
        self,
        start_date: str,
        end_date: str,
        *,
        category_names: list[str] | None = None,
        merchant_names: list[str] | None = None,
        account_names: list[str] | None = None,
    ) -> int:
        return self._with_api(
            lambda api: api.get_filtered_transaction_count(
                start_date,
                end_date,
                category_names=category_names,
                merchant_names=merchant_names,
                account_names=account_names,
            )
        )

    def get_income_summary(self, start_date: str, end_date: str) -> IncomeSummary:
        return self._with_api(lambda api: api.get_income_summary(start_date, end_date))

    def get_net_flow(self, start_date: str, end_date: str) -> int:
        return self._with_api(lambda api: api.get_net_flow(start_date, end_date))


class _ScopingGoalService:
    """Minimal GoalService surface for goal_parse; each call uses its own connection."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    def get_goal(self, goal_id: int) -> GoalRecord:
        with scoped_connection(self._db_path) as conn:
            return GoalService(conn).get_goal(goal_id)


async def _goal_parse(
    config: CoreServiceConfig,
    message: str | None,
    structured_input: dict[str, object] | None,
    review_date: str | None,
) -> dict[str, object]:
    normalized_message = None
    if message is not None:
        normalized_message = message.strip()
        if not normalized_message:
            raise InvalidInputError("message must be non-empty after trimming")
        if len(normalized_message) > 500:
            raise InvalidInputError("message must be at most 500 characters")

    effective_review_date = resolve_review_date(review_date)
    llm = _resolve_goal_capture_llm(config)
    with scoped_connection(config.db_path) as conn:
        goal_service = GoalService(conn)
        active_goals = goal_service.list_active_goals(effective_review_date)
        paused_goals = [
            goal
            for goal in goal_service.list_goals(status="paused")
            if _goal_is_available_on(goal, effective_review_date)
        ]
        goals = active_goals + paused_goals

    finance_api = _ScopingFinanceReadAPI(config.db_path)
    scoped_goal_service = _ScopingGoalService(config.db_path)
    result = await parse_goal_input(
        review_date=effective_review_date,
        finance_api=cast(FinanceReadInterface, finance_api),
        goal_service=cast(GoalService, scoped_goal_service),
        goals=goals,
        message=normalized_message,
        structured_input=structured_input,
        llm=llm,
    )
    return _goal_parse_result_to_dict(result)


def _goal_is_available_on(goal: GoalRecord, review_date: str) -> bool:
    review_point = date.fromisoformat(review_date)
    if review_point < date.fromisoformat(goal.starts_on):
        return False
    return not (goal.ends_on is not None and review_point > date.fromisoformat(goal.ends_on))


def _goal_record_to_dict(goal: GoalRecord) -> dict[str, object]:
    return asdict(goal)


def _goal_progress_to_dict(progress: GoalProgress | None) -> dict[str, object] | None:
    if progress is None:
        return None
    return asdict(progress)


def _goal_parse_result_to_dict(result: GoalCaptureResult) -> dict[str, object]:
    data: dict[str, object] = {
        "result_type": result.result_type,
    }
    if result.assistant_message is not None:
        data["assistant_message"] = result.assistant_message
    if result.action is not None:
        data["action"] = result.action
    if result.payload is not None:
        data["payload"] = result.payload
    if result.response_template is not None:
        data["response_template"] = result.response_template
    if result.response_slots is not None:
        data["response_slots"] = result.response_slots
    if result.goal_id is not None:
        data["goal_id"] = result.goal_id
    if result.clarification_type is not None:
        data["clarification_type"] = result.clarification_type
    if result.clarification_template is not None:
        data["clarification_template"] = result.clarification_template
    if result.clarification_slots is not None:
        data["clarification_slots"] = result.clarification_slots
    if result.question is not None:
        data["question"] = result.question
    if result.options is not None:
        data["options"] = [_goal_capture_option_to_dict(option) for option in result.options]
    if result.resume_payload is not None:
        data["resume_payload"] = result.resume_payload
    return data


def _goal_capture_option_to_dict(option: GoalCaptureOption) -> dict[str, object]:
    if option.kind in {"category", "merchant"}:
        return {
            "kind": option.kind,
            "label": option.label,
            "payload_fragment": option.payload_fragment,
        }
    return {
        "kind": option.kind,
        "goal_id": option.goal_id,
        "title": option.title,
        "label": option.label,
        "period": option.period,
        "target_value": option.target_value,
        "status": option.status,
        "filter_summary": option.filter_summary,
    }


def _resolve_goal_capture_llm(config: CoreServiceConfig) -> JSONLLMInterface | None:
    return create_llm(db_path=config.db_path)
