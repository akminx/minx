"""Memory MCP tools: list / get / create / confirm / reject / expire / candidates."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import InvalidInputError, ToolResponse, wrap_tool_call
from minx_mcp.core.memory_service import MemoryService, memory_record_as_dict
from minx_mcp.core.tools._shared import CoreServiceConfig, coerce_limit
from minx_mcp.db import scoped_connection
from minx_mcp.validation import require_non_empty, require_payload_object

__all__ = ["register_memory_tools"]


def register_memory_tools(mcp: FastMCP, config: CoreServiceConfig) -> None:
    @mcp.tool(name="memory_list")
    def memory_list_tool(
        status: str | None = None,
        memory_type: str | None = None,
        scope: str | None = None,
        limit: int = 100,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_list(config, status, memory_type, scope, limit),
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
    def memory_expire_tool(
        memory_id: int,
        reason: str = "",
        actor: str = "system",
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_expire(config, memory_id, reason, actor),
            tool_name="memory_expire",
        )

    @mcp.tool(name="get_pending_memory_candidates")
    def get_pending_memory_candidates_tool(
        scope: str | None = None,
        limit: int = 50,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _get_pending_memory_candidates(config, scope, limit),
            tool_name="get_pending_memory_candidates",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_list(
    config: CoreServiceConfig,
    status: str | None,
    memory_type: str | None,
    scope: str | None,
    limit: int,
) -> dict[str, object]:
    effective_status = _normalize_optional_filter(status)
    effective_type = _normalize_optional_filter(memory_type)
    effective_scope = _normalize_optional_filter(scope)
    lim = coerce_limit(limit, maximum=500)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        service.prune_expired_memories()
        conn.commit()
        if effective_status == "active":
            rows = service.list_active_memories(
                memory_type=effective_type,
                scope=effective_scope,
                limit=lim,
            )
        else:
            rows = service.list_memories(
                status=effective_status,
                memory_type=effective_type,
                scope=effective_scope,
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


def _memory_expire(
    config: CoreServiceConfig,
    memory_id: int,
    reason: str,
    actor: str = "system",
) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.expire_memory(mid, actor=actor, reason=reason)
        return {"memory": memory_record_as_dict(record)}


def _get_pending_memory_candidates(
    config: CoreServiceConfig,
    scope: str | None,
    limit: int,
) -> dict[str, object]:
    effective_scope = _normalize_optional_filter(scope)
    lim = coerce_limit(limit, maximum=500)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        service.prune_expired_memories()
        conn.commit()
        rows = service.list_pending_candidates(scope=effective_scope, limit=lim)
        return {"memories": [memory_record_as_dict(r) for r in rows]}


def _normalize_optional_filter(value: str | None) -> str | None:
    """Treat whitespace-only strings as "no filter" for MCP tool ergonomics."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _coerce_memory_id(memory_id: int) -> int:
    if not isinstance(memory_id, int) or isinstance(memory_id, bool):
        raise InvalidInputError("memory_id must be an integer")
    if memory_id < 1:
        raise InvalidInputError("memory_id must be positive")
    return memory_id


def _coerce_confidence(value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidInputError("confidence must be a number")
    result = float(value)
    if result < 0 or result > 1:
        raise InvalidInputError("confidence must be between 0 and 1 inclusive")
    return result
