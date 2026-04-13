from __future__ import annotations

import asyncio
import json

from minx_mcp.meals.server import create_meals_server
from minx_mcp.meals.service import MealsService


def _call(server, tool_name: str, args: dict[str, object]) -> dict[str, object]:
    result = asyncio.run(server.call_tool(tool_name, args))
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[1]
    if isinstance(result, list) and result and hasattr(result[0], "text"):
        return json.loads(result[0].text)
    return result


def test_meals_server_registers_expected_tools(db_path, tmp_path) -> None:
    server = create_meals_server(MealsService(db_path, vault_root=tmp_path))
    tool_names = [tool.name for tool in asyncio.run(server.list_tools())]

    assert "meal_log" in tool_names
    assert "pantry_add" in tool_names
    assert "pantry_list" in tool_names
    assert "recommend_recipes" in tool_names


def test_meal_log_tool(db_path, tmp_path) -> None:
    server = create_meals_server(MealsService(db_path, vault_root=tmp_path))

    result = _call(
        server,
        "meal_log",
        {
            "meal_kind": "lunch",
            "occurred_at": "2026-04-12T12:00:00Z",
            "summary": "Chicken salad",
        },
    )

    assert result["success"] is True
    assert result["data"]["meal"]["meal_kind"] == "lunch"


def test_launcher_server_manifest_includes_meals() -> None:
    from minx_mcp.launcher import SERVERS

    names = [server["name"] for server in SERVERS]
    assert names == ["minx-core", "minx-finance", "minx-meals"]
    assert all("module" in server for server in SERVERS)
