from __future__ import annotations

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import ToolResponse, wrap_tool_call
from minx_mcp.training.service import TrainingService


def create_training_server(service: TrainingService) -> FastMCP:
    mcp = FastMCP("minx-training", stateless_http=True, json_response=True)

    @mcp.tool(name="training_exercise_upsert")
    def training_exercise_upsert(
        display_name: str,
        muscle_group: str | None = None,
        is_compound: bool | None = None,
        notes: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "exercise": asdict(
                    service.upsert_exercise(
                        display_name=display_name,
                        muscle_group=muscle_group,
                        is_compound=is_compound,
                        notes=notes,
                    )
                )
            },
            tool_name="training_exercise_upsert",
        )

    @mcp.tool(name="training_exercise_list")
    def training_exercise_list() -> ToolResponse:
        return wrap_tool_call(
            lambda: {"exercises": [asdict(exercise) for exercise in service.list_exercises()]},
            tool_name="training_exercise_list",
        )

    @mcp.tool(name="training_program_upsert")
    def training_program_upsert(
        name: str,
        description: str | None = None,
        days: list[dict[str, object]] | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "program": asdict(
                    service.upsert_program(
                        name=name,
                        description=description,
                        days=days,
                    )
                )
            },
            tool_name="training_program_upsert",
        )

    @mcp.tool(name="training_program_activate")
    def training_program_activate(program_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: {"program": asdict(service.activate_program(program_id))},
            tool_name="training_program_activate",
        )

    @mcp.tool(name="training_program_get")
    def training_program_get(program_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: {"program": asdict(service.get_program(program_id))},
            tool_name="training_program_get",
        )

    @mcp.tool(name="training_session_log")
    def training_session_log(
        occurred_at: str,
        sets: list[dict[str, object]],
        program_id: int | None = None,
        notes: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "session": asdict(
                    service.log_session(
                        occurred_at=occurred_at,
                        sets=sets,
                        program_id=program_id,
                        notes=notes,
                    )
                )
            },
            tool_name="training_session_log",
        )

    @mcp.tool(name="training_session_list")
    def training_session_list(
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "sessions": [
                    asdict(session)
                    for session in service.list_sessions(
                        start_date=start_date,
                        end_date=end_date,
                        limit=limit,
                    )
                ]
            },
            tool_name="training_session_list",
        )

    @mcp.tool(name="training_progress_summary")
    def training_progress_summary(
        as_of: str | None = None,
        lookback_days: int = 7,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "summary": asdict(
                    service.get_progress_summary(
                        as_of=as_of,
                        lookback_days=lookback_days,
                    )
                )
            },
            tool_name="training_progress_summary",
        )

    @mcp.resource("health://status")
    def health_status() -> str:
        import json

        return json.dumps({"status": "ok", "server": "minx-training"})

    return mcp
