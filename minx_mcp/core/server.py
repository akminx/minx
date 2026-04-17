from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Protocol

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import (
    ConflictError,
    InvalidInputError,
    NotFoundError,
    ToolResponse,
    wrap_async_tool_call,
    wrap_tool_call,
)
from minx_mcp.core.goal_parse import parse_goal_input
from minx_mcp.core.goal_progress import build_progress_for_goal
from minx_mcp.core.goals import GoalService
from minx_mcp.core.history import get_insight_history
from minx_mcp.core.llm import create_llm
from minx_mcp.core.memory_service import MemoryService, memory_record_as_dict
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
from minx_mcp.validation import (
    require_non_empty,
    require_payload_object,
    resolve_date_or_today,
    validate_iso_date,
)
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
        return await wrap_async_tool_call(
            lambda: _daily_snapshot(config, review_date, force),
            tool_name="get_daily_snapshot",
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
            ),
            tool_name="get_insight_history",
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

    @mcp.resource("health://status")
    def health_status() -> str:
        return json.dumps({"status": "ok", "server": "minx-core"})

    @mcp.tool(name="persist_note")
    def persist_note(
        relative_path: str,
        content: str,
        overwrite: bool = False,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _persist_note(config, relative_path, content, overwrite),
            tool_name="persist_note",
        )

    @mcp.tool(name="memory_list")
    def memory_list_tool(
        status: str | None = None,
        memory_type: str | None = None,
        limit: int = 100,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_list(config, status, memory_type, limit),
            tool_name="memory_list",
        )

    @mcp.tool(name="memory_get")
    def memory_get_tool(memory_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_get(config, memory_id),
            tool_name="memory_get",
        )

    @mcp.tool(name="memory_create")
    def memory_create_tool(
        memory_type: str,
        scope: str,
        subject: str,
        confidence: float | int,
        payload: object,
        source: str,
        reason: str = "",
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_create(
                config,
                memory_type,
                scope,
                subject,
                confidence,
                payload,
                source,
                reason,
            ),
            tool_name="memory_create",
        )

    @mcp.tool(name="memory_confirm")
    def memory_confirm_tool(memory_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_confirm(config, memory_id),
            tool_name="memory_confirm",
        )

    @mcp.tool(name="memory_reject")
    def memory_reject_tool(memory_id: int, reason: str = "") -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_reject(config, memory_id, reason),
            tool_name="memory_reject",
        )

    @mcp.tool(name="memory_expire")
    def memory_expire_tool(memory_id: int, reason: str = "") -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_expire(config, memory_id, reason),
            tool_name="memory_expire",
        )

    @mcp.tool(name="get_pending_memory_candidates")
    def get_pending_memory_candidates_tool(limit: int = 50) -> ToolResponse:
        return wrap_tool_call(
            lambda: _get_pending_memory_candidates(config, limit),
            tool_name="get_pending_memory_candidates",
        )

    @mcp.tool(name="list_snapshot_archives")
    def list_snapshot_archives_tool(
        review_date: str | None = None,
        limit: int = 30,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _list_snapshot_archives(config, review_date, limit),
            tool_name="list_snapshot_archives",
        )

    @mcp.tool(name="get_snapshot_archive")
    def get_snapshot_archive_tool(archive_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _get_snapshot_archive(config, archive_id),
            tool_name="get_snapshot_archive",
        )

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


def _memory_list(
    config: CoreServiceConfig,
    status: str | None,
    memory_type: str | None,
    limit: int,
) -> dict[str, object]:
    effective_status = status.strip() if isinstance(status, str) else None
    if effective_status == "":
        effective_status = None
    effective_type = memory_type.strip() if isinstance(memory_type, str) else None
    if effective_type == "":
        effective_type = None
    lim = _coerce_limit(limit, maximum=500)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        rows = service.list_memories(
            status=effective_status,
            memory_type=effective_type,
            limit=lim,
        )
        return {"memories": [memory_record_as_dict(r) for r in rows]}


def _memory_get(config: CoreServiceConfig, memory_id: int) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.get_memory(mid)
        return {"memory": memory_record_as_dict(record)}


def _memory_create(
    config: CoreServiceConfig,
    memory_type: str,
    scope: str,
    subject: str,
    confidence: float | int,
    payload: object,
    source: str,
    reason: str,
) -> dict[str, object]:
    mt = require_non_empty("memory_type", memory_type)
    sc = require_non_empty("scope", scope)
    sj = require_non_empty("subject", subject)
    src = require_non_empty("source", source)
    body = require_payload_object(payload, field_name="payload")
    conf = _coerce_confidence(confidence)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.create_memory(
            memory_type=mt,
            scope=sc,
            subject=sj,
            confidence=conf,
            payload=body,
            source=src,
            reason=reason,
            actor="user",
        )
        return {"memory": memory_record_as_dict(record)}


def _memory_confirm(config: CoreServiceConfig, memory_id: int) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.confirm_memory(mid, actor="user")
        return {"memory": memory_record_as_dict(record)}


def _memory_reject(config: CoreServiceConfig, memory_id: int, reason: str) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.reject_memory(mid, actor="user", reason=reason)
        return {"memory": memory_record_as_dict(record)}


def _memory_expire(config: CoreServiceConfig, memory_id: int, reason: str) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.expire_memory(mid, actor="user", reason=reason)
        return {"memory": memory_record_as_dict(record)}


def _get_pending_memory_candidates(config: CoreServiceConfig, limit: int) -> dict[str, object]:
    lim = _coerce_limit(limit, maximum=500)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        rows = service.list_pending_candidates(limit=lim)
        return {"memories": [memory_record_as_dict(r) for r in rows]}


def _list_snapshot_archives(
    config: CoreServiceConfig,
    review_date: str | None,
    limit: int,
) -> dict[str, object]:
    lim = _coerce_limit(limit, maximum=500)
    filter_date: str | None = None
    if review_date is not None:
        stripped = review_date.strip()
        if stripped == "":
            raise InvalidInputError("review_date must not be empty when provided")
        filter_date = validate_iso_date(stripped, field_name="review_date").isoformat()
    with scoped_connection(Path(config.db_path)) as conn:
        if filter_date is None:
            rows = conn.execute(
                """
                SELECT id, review_date, generated_at, content_hash, source
                FROM snapshot_archives
                ORDER BY generated_at DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, review_date, generated_at, content_hash, source
                FROM snapshot_archives
                WHERE review_date = ?
                ORDER BY generated_at DESC
                LIMIT ?
                """,
                (filter_date, lim),
            ).fetchall()
        archives = [
            {
                "id": int(row["id"]),
                "review_date": row["review_date"],
                "generated_at": row["generated_at"],
                "content_hash": row["content_hash"],
                "source": row["source"],
            }
            for row in rows
        ]
        return {"archives": archives}


def _get_snapshot_archive(config: CoreServiceConfig, archive_id: int) -> dict[str, object]:
    aid = _coerce_archive_id(archive_id)
    with scoped_connection(Path(config.db_path)) as conn:
        row = conn.execute(
            """
            SELECT id, review_date, generated_at, snapshot_json, content_hash, source
            FROM snapshot_archives
            WHERE id = ?
            """,
            (aid,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"snapshot archive {aid} not found")
        snapshot_payload = json.loads(row["snapshot_json"])
        return {
            "archive": {
                "id": int(row["id"]),
                "review_date": row["review_date"],
                "generated_at": row["generated_at"],
                "content_hash": row["content_hash"],
                "source": row["source"],
                "snapshot": snapshot_payload,
            }
        }


def _coerce_archive_id(archive_id: int) -> int:
    if not isinstance(archive_id, int) or isinstance(archive_id, bool):
        raise InvalidInputError("archive_id must be an integer")
    if archive_id < 1:
        raise InvalidInputError("archive_id must be positive")
    return archive_id


def _coerce_memory_id(memory_id: int) -> int:
    if not isinstance(memory_id, int) or isinstance(memory_id, bool):
        raise InvalidInputError("memory_id must be an integer")
    if memory_id < 1:
        raise InvalidInputError("memory_id must be positive")
    return memory_id


def _coerce_confidence(value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidInputError("confidence must be a number")
    result = float(value)
    if result < 0 or result > 1:
        raise InvalidInputError("confidence must be between 0 and 1 inclusive")
    return result


def _coerce_limit(value: int, *, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError("limit must be an integer")
    if value < 1 or value > maximum:
        raise InvalidInputError(f"limit must be between 1 and {maximum}")
    return value


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
