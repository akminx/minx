from __future__ import annotations

import asyncio
import json

from minx_mcp.training.server import create_training_server
from minx_mcp.training.service import TrainingService


def _call(server, tool_name: str, args: dict[str, object]) -> dict[str, object]:
    result = asyncio.run(server.call_tool(tool_name, args))
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    if isinstance(result, list) and result and hasattr(result[0], "text"):
        return json.loads(result[0].text)
    return result


def test_training_server_registers_expected_tools(db_path) -> None:
    server = create_training_server(TrainingService(db_path))
    tool_names = [tool.name for tool in asyncio.run(server.list_tools())]

    assert "training_exercise_upsert" in tool_names
    assert "training_exercise_list" in tool_names
    assert "training_program_upsert" in tool_names
    assert "training_program_activate" in tool_names
    assert "training_program_get" in tool_names
    assert "training_session_log" in tool_names
    assert "training_session_list" in tool_names
    assert "training_progress_summary" in tool_names


def test_training_tool_roundtrip_for_program_and_session(db_path) -> None:
    server = create_training_server(TrainingService(db_path))
    exercise = _call(
        server,
        "training_exercise_upsert",
        {"display_name": "Deadlift", "is_compound": True},
    )
    program = _call(
        server,
        "training_program_upsert",
        {
            "name": "Strength Block",
            "days": [
                {
                    "day_index": 1,
                    "label": "Pull",
                    "exercises": [
                        {
                            "exercise_id": exercise["data"]["exercise"]["id"],
                            "target_sets": 3,
                            "target_reps": 5,
                        }
                    ],
                }
            ],
        },
    )
    activate = _call(
        server,
        "training_program_activate",
        {"program_id": program["data"]["program"]["id"]},
    )
    logged = _call(
        server,
        "training_session_log",
        {
            "occurred_at": "2026-04-13T07:00:00Z",
            "program_id": program["data"]["program"]["id"],
            "sets": [
                {
                    "exercise_id": exercise["data"]["exercise"]["id"],
                    "reps": 5,
                    "weight_kg": 140.0,
                }
            ],
        },
    )
    summary = _call(server, "training_progress_summary", {"as_of": "2026-04-13"})

    assert exercise["success"] is True
    assert program["success"] is True
    assert activate["data"]["program"]["is_active"] is True
    assert logged["success"] is True
    assert summary["success"] is True
    assert summary["data"]["summary"]["sessions_logged"] == 1


def test_training_session_log_returns_invalid_input_for_empty_sets(db_path) -> None:
    server = create_training_server(TrainingService(db_path))
    result = _call(
        server,
        "training_session_log",
        {"occurred_at": "2026-04-13T07:00:00Z", "sets": []},
    )

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_training_program_upsert_returns_invalid_input_for_duplicate_day_index(db_path) -> None:
    server = create_training_server(TrainingService(db_path))
    result = _call(
        server,
        "training_program_upsert",
        {
            "name": "Dup Program",
            "days": [
                {"day_index": 1, "label": "A", "exercises": []},
                {"day_index": 1, "label": "B", "exercises": []},
            ],
        },
    )

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"
