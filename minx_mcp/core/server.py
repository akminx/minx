from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Protocol

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import (
    ConflictError,
    InvalidInputError,
    ToolResponse,
    wrap_async_tool_call,
    wrap_tool_call,
)
from minx_mcp.core.goal_parse import parse_goal_input
from minx_mcp.core.goal_progress import build_progress_for_goal
from minx_mcp.core.goals import GoalService
from minx_mcp.core.history import get_insight_history
from minx_mcp.core.llm import create_llm
from minx_mcp.core.models import (
    GoalCaptureOption,
    GoalCaptureResult,
    GoalCreateInput,
    GoalProgress,
    GoalRecord,
    GoalUpdateInput,
    JSONLLMInterface,
    SnapshotContext,
)
from minx_mcp.core.snapshot import build_daily_snapshot
from minx_mcp.core.trajectory import get_goal_trajectory
from minx_mcp.db import scoped_connection
from minx_mcp.finance.read_api import FinanceReadAPI
from minx_mcp.validation import resolve_date_or_today
from minx_mcp.vault_writer import VaultWriter


class CoreServiceConfig(Protocol):
    @property
    def db_path(self) -> Path: ...

    @property
    def vault_path(self) -> Path: ...


def create_core_server(config: CoreServiceConfig) -> FastMCP:
    mcp = FastMCP("minx-core", stateless_http=True, json_response=True)

    @mcp.tool(name="get_daily_snapshot")
    async def get_daily_snapshot_tool(
        review_date: str | None = None,
        force: bool = False,
    ) -> ToolResponse:
        return await wrap_async_tool_call(lambda: _daily_snapshot(config, review_date, force))

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
            )
        )

    @mcp.tool(name="goal_list")
    def goal_list(status: str | None = None) -> ToolResponse:
        return wrap_tool_call(lambda: _goal_list(config, status))

    @mcp.tool(name="goal_get")
    def goal_get(goal_id: int, review_date: str | None = None) -> ToolResponse:
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
            )
        )

    @mcp.tool(name="goal_archive")
    def goal_archive(goal_id: int) -> ToolResponse:
        return wrap_tool_call(lambda: _goal_archive(config, goal_id))

    @mcp.tool(name="goal_parse")
    async def goal_parse(
        message: str | None = None,
        structured_input: dict[str, object] | None = None,
        review_date: str | None = None,
    ) -> ToolResponse:
        return await wrap_async_tool_call(
            lambda: _goal_parse(config, message, structured_input, review_date)
        )

    @mcp.tool(name="get_insight_history")
    def insight_history(
        days: int = 28,
        insight_type: str | None = None,
        goal_id: int | None = None,
        end_date: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: get_insight_history(
                config.db_path,
                days=days,
                insight_type=insight_type,
                goal_id=goal_id,
                end_date=end_date,
            )
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
            )
        )

    @mcp.resource("health://status")
    def health_status() -> str:
        import json

        return json.dumps({"status": "ok", "server": "minx-core"})

    @mcp.tool(name="persist_note")
    def persist_note(
        relative_path: str,
        content: str,
        overwrite: bool = False,
    ) -> ToolResponse:
        return wrap_tool_call(lambda: _persist_note(config, relative_path, content, overwrite))

    return mcp


async def _daily_snapshot(
    config: CoreServiceConfig,
    review_date: str | None,
    force: bool,
) -> dict[str, object]:
    effective_date = _resolve_review_date(review_date)
    ctx = SnapshotContext(
        db_path=config.db_path,
        finance_api=None,
    )
    artifact = await build_daily_snapshot(effective_date, ctx, force=force)
    data = asdict(artifact)
    if data["persistence_warning"] is None:
        data.pop("persistence_warning")
    return data


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
    effective_review_date = _resolve_review_date(review_date)
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

    effective_review_date = _resolve_review_date(review_date)
    with scoped_connection(config.db_path) as conn:
        goal_service = GoalService(conn)
        active_goals = goal_service.list_active_goals(effective_review_date)
        paused_goals = [
            goal
            for goal in goal_service.list_goals(status="paused")
            if _goal_is_available_on(goal, effective_review_date)
        ]
        goals = active_goals + paused_goals
        llm = _resolve_goal_capture_llm(config)
        result = await parse_goal_input(
            review_date=effective_review_date,
            finance_api=FinanceReadAPI(conn),
            goal_service=goal_service,
            goals=goals,
            message=normalized_message,
            structured_input=structured_input,
            llm=llm,
        )
        return _goal_parse_result_to_dict(result)


def _resolve_review_date(review_date: str | None) -> str:
    return resolve_date_or_today(review_date, field_name="review_date")


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


def _resolve_goal_capture_llm(config: CoreServiceConfig) -> JSONLLMInterface | None:
    configured = create_llm(db_path=config.db_path)
    if not isinstance(configured, JSONLLMInterface):
        return None
    return configured


def _persist_note(
    config: CoreServiceConfig,
    relative_path: str,
    content: str,
    overwrite: bool,
) -> dict[str, object]:
    writer = VaultWriter(config.vault_path, ("Minx",))
    try:
        resolved = writer.resolve_path(relative_path)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    existed = resolved.exists()
    if existed and not overwrite:
        raise ConflictError("note already exists", data={"path": str(resolved)})
    writer.write_markdown(relative_path, content)
    return {"path": str(resolved), "overwritten" if existed else "created": True}
