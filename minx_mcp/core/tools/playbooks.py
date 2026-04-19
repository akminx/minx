"""Playbook registry resource + playbook run audit MCP tools."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import ToolResponse, wrap_tool_call
from minx_mcp.core import playbooks as playbook_api
from minx_mcp.core.tools._shared import CoreServiceConfig
from minx_mcp.db import scoped_connection

__all__ = ["register_playbook_tools"]


def register_playbook_tools(mcp: FastMCP, config: CoreServiceConfig) -> None:
    @mcp.resource("playbook://registry")
    def playbook_registry() -> str:
        return json.dumps(playbook_api.playbook_registry_payload())

    @mcp.tool(name="start_playbook_run")
    def start_playbook_run_tool(
        playbook_id: str,
        harness: str,
        trigger_type: str,
        trigger_ref: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _start_playbook_run(
                config,
                playbook_id=playbook_id,
                harness=harness,
                trigger_type=trigger_type,
                trigger_ref=trigger_ref,
            ),
            tool_name="start_playbook_run",
        )

    @mcp.tool(name="complete_playbook_run")
    def complete_playbook_run_tool(
        run_id: int,
        status: str,
        conditions_met: bool,
        action_taken: bool,
        result_json: str | None = None,
        error_message: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _complete_playbook_run(
                config,
                run_id=run_id,
                status=status,
                conditions_met=conditions_met,
                action_taken=action_taken,
                result_json=result_json,
                error_message=error_message,
            ),
            tool_name="complete_playbook_run",
        )

    @mcp.tool(name="log_playbook_run")
    def log_playbook_run_tool(
        playbook_id: str,
        harness: str,
        trigger_type: str,
        trigger_ref: str | None = None,
        status: str = "succeeded",
        conditions_met: bool = True,
        action_taken: bool = True,
        result_json: str | None = None,
        error_message: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _log_playbook_run(
                config,
                playbook_id=playbook_id,
                harness=harness,
                trigger_type=trigger_type,
                trigger_ref=trigger_ref,
                status=status,
                conditions_met=conditions_met,
                action_taken=action_taken,
                result_json=result_json,
                error_message=error_message,
            ),
            tool_name="log_playbook_run",
        )

    @mcp.tool(name="playbook_history")
    def playbook_history_tool(
        playbook_id: str | None = None,
        harness: str | None = None,
        status: str | None = None,
        since: str | None = None,
        days: int = 30,
        limit: int = 200,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _playbook_history(
                config,
                playbook_id=playbook_id,
                harness=harness,
                status=status,
                since=since,
                days=days,
                limit=limit,
            ),
            tool_name="playbook_history",
        )

    @mcp.tool(name="playbook_reconcile_crashed")
    def playbook_reconcile_crashed_tool(stale_after_minutes: int = 15) -> ToolResponse:
        return wrap_tool_call(
            lambda: _playbook_reconcile_crashed(config, stale_after_minutes=stale_after_minutes),
            tool_name="playbook_reconcile_crashed",
        )


def _start_playbook_run(
    config: CoreServiceConfig,
    *,
    playbook_id: str,
    harness: str,
    trigger_type: str,
    trigger_ref: str | None,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        run_id = playbook_api.start_playbook_run(
            conn,
            playbook_id=playbook_id,
            harness=harness,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
        )
        return {"run_id": run_id}


def _complete_playbook_run(
    config: CoreServiceConfig,
    *,
    run_id: int,
    status: str,
    conditions_met: bool,
    action_taken: bool,
    result_json: str | None,
    error_message: str | None,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        completed_id = playbook_api.complete_playbook_run(
            conn,
            run_id=run_id,
            status=status,
            conditions_met=conditions_met,
            action_taken=action_taken,
            result_json=result_json,
            error_message=error_message,
        )
        return {"run_id": completed_id}


def _log_playbook_run(
    config: CoreServiceConfig,
    *,
    playbook_id: str,
    harness: str,
    trigger_type: str,
    trigger_ref: str | None,
    status: str,
    conditions_met: bool,
    action_taken: bool,
    result_json: str | None,
    error_message: str | None,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        run_id = playbook_api.log_playbook_run(
            conn,
            playbook_id=playbook_id,
            harness=harness,
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            status=status,
            conditions_met=conditions_met,
            action_taken=action_taken,
            result_json=result_json,
            error_message=error_message,
        )
        return {"run_id": run_id}


def _playbook_history(
    config: CoreServiceConfig,
    *,
    playbook_id: str | None,
    harness: str | None,
    status: str | None,
    since: str | None,
    days: int,
    limit: int,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return playbook_api.playbook_history(
            conn,
            playbook_id=playbook_id,
            harness=harness,
            status=status,
            since=since,
            days=days,
            limit=limit,
        )


def _playbook_reconcile_crashed(
    config: CoreServiceConfig,
    *,
    stale_after_minutes: int,
) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return playbook_api.playbook_reconcile_crashed(
            conn,
            stale_after_minutes=stale_after_minutes,
        )
