"""Investigation lifecycle MCP tools and resources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import InvalidInputError, ToolResponse, wrap_tool_call
from minx_mcp.core import investigations as investigation_api
from minx_mcp.core.tools._shared import CoreServiceConfig
from minx_mcp.db import scoped_connection

__all__ = ["register_investigation_tools"]


def register_investigation_tools(mcp: FastMCP, config: CoreServiceConfig) -> None:
    @mcp.resource("investigation://recent")
    def investigation_recent_resource() -> str:
        with scoped_connection(Path(config.db_path)) as conn:
            return json.dumps(investigation_api.recent_resource_payload(conn))

    @mcp.resource("investigation://{investigation_id}")
    def investigation_by_id_resource(investigation_id: str) -> str:
        try:
            parsed_id = int(investigation_id)
        except ValueError as exc:
            raise InvalidInputError("investigation_id must be an integer") from exc
        with scoped_connection(Path(config.db_path)) as conn:
            return json.dumps(
                investigation_api.investigation_resource_payload(
                    conn,
                    investigation_id=parsed_id,
                )
            )

    @mcp.tool(name="start_investigation")
    def start_investigation_tool(
        kind: str,
        question: str,
        context_json: dict[str, Any] | None = None,
        harness: str = "hermes",
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _start_investigation(
                config,
                kind=kind,
                question=question,
                context_json=context_json,
                harness=harness,
            ),
            tool_name="start_investigation",
        )

    @mcp.tool(name="append_investigation_step")
    def append_investigation_step_tool(investigation_id: int, step_json: dict[str, Any]) -> ToolResponse:
        return wrap_tool_call(
            lambda: _append_investigation_step(config, investigation_id=investigation_id, step_json=step_json),
            tool_name="append_investigation_step",
        )

    @mcp.tool(name="complete_investigation")
    def complete_investigation_tool(
        investigation_id: int,
        status: str,
        answer_md: str | None = None,
        citation_refs: list[dict[str, Any]] | None = None,
        tool_call_count: int | None = None,
        token_input: int | None = None,
        token_output: int | None = None,
        cost_usd: float | None = None,
        error_message: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _complete_investigation(
                config,
                investigation_id=investigation_id,
                status=status,
                answer_md=answer_md,
                citation_refs=citation_refs,
                tool_call_count=tool_call_count,
                token_input=token_input,
                token_output=token_output,
                cost_usd=cost_usd,
                error_message=error_message,
            ),
            tool_name="complete_investigation",
        )

    @mcp.tool(name="log_investigation")
    def log_investigation_tool(
        kind: str,
        question: str,
        context_json: dict[str, Any] | None = None,
        harness: str = "hermes",
        trajectory_json: list[dict[str, Any]] | None = None,
        status: str = "succeeded",
        answer_md: str | None = None,
        citation_refs: list[dict[str, Any]] | None = None,
        tool_call_count: int | None = None,
        token_input: int | None = None,
        token_output: int | None = None,
        cost_usd: float | None = None,
        error_message: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _log_investigation(
                config,
                kind=kind,
                question=question,
                context_json=context_json,
                harness=harness,
                trajectory_json=trajectory_json,
                status=status,
                answer_md=answer_md,
                citation_refs=citation_refs,
                tool_call_count=tool_call_count,
                token_input=token_input,
                token_output=token_output,
                cost_usd=cost_usd,
                error_message=error_message,
            ),
            tool_name="log_investigation",
        )

    @mcp.tool(name="investigation_history")
    def investigation_history_tool(
        kind: str | None = None,
        harness: str | None = None,
        status: str | None = None,
        since: str | None = None,
        days: int = 30,
        limit: int = 100,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _investigation_history(
                config,
                kind=kind,
                harness=harness,
                status=status,
                since=since,
                days=days,
                limit=limit,
            ),
            tool_name="investigation_history",
        )

    @mcp.tool(name="investigation_get")
    def investigation_get_tool(investigation_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _investigation_get(config, investigation_id=investigation_id),
            tool_name="investigation_get",
        )


def _start_investigation(
    config: CoreServiceConfig,
    *,
    kind: str,
    question: str,
    context_json: dict[str, Any] | None,
    harness: str,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return investigation_api.start_investigation(
            conn,
            kind=kind,
            question=question,
            context_json=context_json,
            harness=harness,
        )


def _append_investigation_step(
    config: CoreServiceConfig,
    *,
    investigation_id: int,
    step_json: dict[str, Any],
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return investigation_api.append_investigation_step(
            conn,
            investigation_id=investigation_id,
            step_json=step_json,
        )


def _complete_investigation(
    config: CoreServiceConfig,
    *,
    investigation_id: int,
    status: str,
    answer_md: str | None,
    citation_refs: list[dict[str, Any]] | None,
    tool_call_count: int | None,
    token_input: int | None,
    token_output: int | None,
    cost_usd: float | None,
    error_message: str | None,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return investigation_api.complete_investigation(
            conn,
            investigation_id=investigation_id,
            status=status,
            answer_md=answer_md,
            citation_refs=citation_refs,
            tool_call_count=tool_call_count,
            token_input=token_input,
            token_output=token_output,
            cost_usd=cost_usd,
            error_message=error_message,
        )


def _log_investigation(
    config: CoreServiceConfig,
    *,
    kind: str,
    question: str,
    context_json: dict[str, Any] | None,
    harness: str,
    trajectory_json: list[dict[str, Any]] | None,
    status: str,
    answer_md: str | None,
    citation_refs: list[dict[str, Any]] | None,
    tool_call_count: int | None,
    token_input: int | None,
    token_output: int | None,
    cost_usd: float | None,
    error_message: str | None,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return investigation_api.log_investigation(
            conn,
            kind=kind,
            question=question,
            context_json=context_json,
            harness=harness,
            trajectory_json=trajectory_json,
            status=status,
            answer_md=answer_md,
            citation_refs=citation_refs,
            tool_call_count=tool_call_count,
            token_input=token_input,
            token_output=token_output,
            cost_usd=cost_usd,
            error_message=error_message,
        )


def _investigation_history(
    config: CoreServiceConfig,
    *,
    kind: str | None,
    harness: str | None,
    status: str | None,
    since: str | None,
    days: int,
    limit: int,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return investigation_api.investigation_history(
            conn,
            kind=kind,
            harness=harness,
            status=status,
            since=since,
            days=days,
            limit=limit,
        )


def _investigation_get(config: CoreServiceConfig, *, investigation_id: int) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return investigation_api.investigation_get(conn, investigation_id=investigation_id)
