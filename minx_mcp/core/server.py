from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Protocol

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import ConflictError, InvalidInputError, wrap_async_tool_call, wrap_tool_call
from minx_mcp.core.goal_capture import capture_goal_message
from minx_mcp.core.goal_progress import build_progress_for_goal
from minx_mcp.core.goals import GoalService
from minx_mcp.core.llm import create_llm
from minx_mcp.core.models import (
    GoalCaptureOption,
    GoalCaptureResult,
    GoalCreateInput,
    GoalProgress,
    GoalRecord,
    GoalUpdateInput,
    ReviewContext,
    ReviewDurabilityError,
)
from minx_mcp.core.review import generate_daily_review
from minx_mcp.core.review_policy import build_protected_review
from minx_mcp.db import get_connection
from minx_mcp.finance.read_api import FinanceReadAPI
from minx_mcp.vault_writer import VaultWriter


class CoreServiceConfig(Protocol):
    @property
    def db_path(self) -> Path: ...

    @property
    def vault_path(self) -> Path: ...


def create_core_server(config: CoreServiceConfig) -> FastMCP:
    mcp = FastMCP("minx-core", stateless_http=True, json_response=True)

    @mcp.tool(name="daily_review")
    async def daily_review(
        review_date: str | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        return await wrap_async_tool_call(
            lambda: _daily_review_tool_call(config, review_date, force),
        )

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
    ) -> dict[str, object]:
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
            )
        )

    @mcp.tool(name="goal_list")
    def goal_list(status: str | None = None) -> dict[str, object]:
        return wrap_tool_call(lambda: _goal_list(config, status))

    @mcp.tool(name="goal_get")
    def goal_get(goal_id: int, review_date: str | None = None) -> dict[str, object]:
        return wrap_tool_call(lambda: _goal_get(config, goal_id, review_date))

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
    ) -> dict[str, object]:
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
            )
        )

    @mcp.tool(name="goal_archive")
    def goal_archive(goal_id: int) -> dict[str, object]:
        return wrap_tool_call(lambda: _goal_archive(config, goal_id))

    @mcp.tool(name="goal_capture")
    async def goal_capture(
        message: str,
        review_date: str | None = None,
    ) -> dict[str, object]:
        return await wrap_async_tool_call(lambda: _goal_capture(config, message, review_date))

    return mcp


async def _daily_review(
    config: CoreServiceConfig,
    review_date: str | None,
    force: bool,
) -> dict[str, object]:
    effective_date = _resolve_review_date(review_date)
    ctx = ReviewContext(
        db_path=config.db_path,
        finance_api=None,
        vault_writer=VaultWriter(config.vault_path, ("Minx",)),
        llm=None,
    )
    artifact = await generate_daily_review(effective_date, ctx, force=force)
    return asdict(build_protected_review(artifact))


async def _daily_review_tool_call(
    config: CoreServiceConfig,
    review_date: str | None,
    force: bool,
) -> dict[str, object]:
    try:
        return await _daily_review(config, review_date, force)
    except ReviewDurabilityError as exc:
        protected = asdict(build_protected_review(exc.artifact))
        protected["recoverable"] = True
        protected["durability_failures"] = [
            {"sink": failure.sink, "error": str(failure.error)}
            for failure in exc.failures
        ]
        raise ConflictError(str(exc), data=protected) from exc


def _goal_create(config: CoreServiceConfig, payload: GoalCreateInput) -> dict[str, object]:
    conn = get_connection(config.db_path)
    try:
        goal = GoalService(conn).create_goal(payload)
        return {"goal": _goal_record_to_dict(goal)}
    finally:
        conn.close()


def _goal_list(config: CoreServiceConfig, status: str | None) -> dict[str, object]:
    conn = get_connection(config.db_path)
    try:
        goals = GoalService(conn).list_goals(status=status)
        return {"goals": [_goal_record_to_dict(goal) for goal in goals]}
    finally:
        conn.close()


def _goal_get(
    config: CoreServiceConfig,
    goal_id: int,
    review_date: str | None,
) -> dict[str, object]:
    effective_review_date = _resolve_review_date(review_date)
    conn = get_connection(config.db_path)
    try:
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
    finally:
        conn.close()


def _goal_update(
    config: CoreServiceConfig,
    goal_id: int,
    payload: GoalUpdateInput,
) -> dict[str, object]:
    conn = get_connection(config.db_path)
    try:
        goal = GoalService(conn).update_goal(goal_id, payload)
        return {"goal": _goal_record_to_dict(goal)}
    finally:
        conn.close()


def _goal_archive(config: CoreServiceConfig, goal_id: int) -> dict[str, object]:
    conn = get_connection(config.db_path)
    try:
        goal = GoalService(conn).archive_goal(goal_id)
        return {"goal": _goal_record_to_dict(goal)}
    finally:
        conn.close()


async def _goal_capture(
    config: CoreServiceConfig,
    message: str,
    review_date: str | None,
) -> dict[str, object]:
    normalized_message = message.strip()
    if not normalized_message:
        raise InvalidInputError("message must be non-empty after trimming")
    if len(normalized_message) > 500:
        raise InvalidInputError("message must be at most 500 characters")

    effective_review_date = _resolve_review_date(review_date)
    conn = get_connection(config.db_path)
    try:
        goal_service = GoalService(conn)
        goals = goal_service.list_goals(status="active") + goal_service.list_goals(status="paused")
        llm = _resolve_goal_capture_llm(config)
        result = await capture_goal_message(
            message=normalized_message,
            review_date=effective_review_date,
            finance_api=FinanceReadAPI(conn),
            goals=goals,
            llm=llm,
        )
        return _goal_capture_result_to_dict(result)
    finally:
        conn.close()


def _resolve_review_date(review_date: str | None) -> str:
    effective_date = review_date if review_date is not None else date.today().isoformat()
    try:
        date.fromisoformat(effective_date)
    except ValueError as exc:
        raise InvalidInputError("review_date must be a valid ISO date") from exc
    return effective_date


def _goal_record_to_dict(goal: GoalRecord) -> dict[str, object]:
    return asdict(goal)


def _goal_progress_to_dict(progress: GoalProgress | None) -> dict[str, object] | None:
    if progress is None:
        return None
    return asdict(progress)


def _goal_capture_result_to_dict(result: GoalCaptureResult) -> dict[str, object]:
    data: dict[str, object] = {
        "result_type": result.result_type,
    }
    if result.assistant_message is not None:
        data["assistant_message"] = result.assistant_message
    if result.action is not None:
        data["action"] = result.action
    if result.payload is not None:
        data["payload"] = result.payload
    if result.goal_id is not None:
        data["goal_id"] = result.goal_id
    if result.clarification_type is not None:
        data["clarification_type"] = result.clarification_type
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
        "goal_id": option.goal_id,
        "title": option.title,
        "period": option.period,
        "target_value": option.target_value,
        "status": option.status,
        "filter_summary": option.filter_summary,
    }


def _resolve_goal_capture_llm(config: CoreServiceConfig) -> object | None:
    configured = create_llm(db_path=config.db_path)
    if configured is None or not callable(getattr(configured, "run_json_prompt", None)):
        return None
    return configured
