from __future__ import annotations

import asyncio
from typing import Any, cast

from minx_mcp.db import get_connection
from minx_mcp.meals.server import create_meals_server
from minx_mcp.meals.service import MealsService
from tests.helpers import call_server as _call


def test_meals_server_registers_expected_tools(db_path, tmp_path) -> None:
    server = create_meals_server(MealsService(db_path, vault_root=tmp_path))
    tool_names = [tool.name for tool in asyncio.run(server.list_tools())]

    assert "meal_log" in tool_names
    assert "pantry_add" in tool_names
    assert "pantry_list" in tool_names
    assert "recommend_recipes" in tool_names
    assert "nutrition_profile_set" in tool_names
    assert "nutrition_profile_get" in tool_names
    assert "recipe_template" in tool_names


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

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT meal_kind, summary FROM meals_meal_entries WHERE id = ?",
            (result["data"]["meal"]["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["meal_kind"] == "lunch"
    assert row["summary"] == "Chicken salad"


def test_launcher_server_manifest_includes_meals() -> None:
    from minx_mcp.launcher import SERVERS

    names = [server["name"] for server in SERVERS]
    assert names == ["minx-core", "minx-finance", "minx-meals", "minx-training"]
    assert all("module" in server for server in SERVERS)


def test_nutrition_profile_tools(db_path, tmp_path) -> None:
    server = create_meals_server(MealsService(db_path, vault_root=tmp_path))

    set_result = _call(
        server,
        "nutrition_profile_set",
        {
            "sex": "male",
            "age_years": 30,
            "height_cm": 180.0,
            "weight_kg": 80.0,
            "activity_level": "moderately_active",
            "goal": "maintenance",
            "calorie_deficit_kcal": 400,
        },
    )
    get_result = _call(server, "nutrition_profile_get", {})

    assert set_result["success"] is True
    assert set_result["data"]["plan"]["targets"]["calorie_target_kcal"] == 2359
    assert get_result["success"] is True
    assert get_result["data"]["plan"]["profile"]["activity_level"] == "moderately_active"


def test_nutrition_profile_set_mcp_default_goal_matches_service(db_path, tmp_path) -> None:
    """Omitting `goal` on the MCP tool must produce the same targets as omitting it on the service."""
    service = MealsService(db_path, vault_root=tmp_path)
    expected = service.set_nutrition_profile(
        sex="male",
        age_years=30,
        height_cm=180.0,
        weight_kg=80.0,
        activity_level="moderately_active",
    )

    server = create_meals_server(MealsService(db_path, vault_root=tmp_path))
    set_result = cast(
        dict[str, Any],
        _call(
            server,
            "nutrition_profile_set",
            {
                "sex": "male",
                "age_years": 30,
                "height_cm": 180.0,
                "weight_kg": 80.0,
                "activity_level": "moderately_active",
            },
        ),
    )

    assert set_result["success"] is True
    assert (
        set_result["data"]["plan"]["targets"]["calorie_target_kcal"]
        == expected.targets.calorie_target_kcal
    )
    assert set_result["data"]["plan"]["profile"]["goal"] == expected.profile.goal


def test_recipe_template_tool_returns_packaged_scaffold(db_path, tmp_path) -> None:
    server = create_meals_server(MealsService(db_path, vault_root=tmp_path))

    result = _call(server, "recipe_template", {})

    assert result["success"] is True
    data = result["data"]
    assert data["filename"] == "recipe-starter.md"
    template = data["template"]
    assert isinstance(template, str)
    assert template.startswith("---\n"), "tool must return the scaffold verbatim, including frontmatter"
    assert "## Ingredients" in template
    assert "## Substitutions" in template
    assert "## Notes" in template
