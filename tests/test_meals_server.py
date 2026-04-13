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
    assert "shopping_list_generate" in tool_names
    assert "recipe_detail" in tool_names


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


def test_shopping_list_generate_tool(db_path, meals_seeder) -> None:
    recipe_id = meals_seeder.recipe(vault_path="Recipes/Salmon.md", title="Salmon Dinner")
    meals_seeder.recipe_ingredient(
        recipe_id=recipe_id,
        display_text="200g salmon",
        normalized_name="salmon",
        quantity=200,
        unit="g",
    )
    service = MealsService(db_path)
    server = create_meals_server(service)

    result = _call(server, "shopping_list_generate", {"recipe_id": recipe_id})

    assert result["success"] is True
    assert result["data"]["shopping_list"]["recipe_title"] == "Salmon Dinner"
    assert result["data"]["shopping_list"]["items"][0]["normalized_name"] == "salmon"


def test_recipe_detail_tool(db_path, meals_seeder) -> None:
    recipe_id = meals_seeder.recipe(
        vault_path="Recipes/Salmon.md",
        title="Salmon Dinner",
        image_ref="Assets/salmon.jpg",
        source_url="https://example.com/salmon",
    )
    meals_seeder.recipe_ingredient(
        recipe_id=recipe_id,
        display_text="200g salmon",
        normalized_name="salmon",
        quantity=200,
        unit="g",
    )
    service = MealsService(db_path)
    service.conn.execute(
        """
        UPDATE meals_recipes
        SET prep_time_minutes = 10,
            cook_time_minutes = 20,
            servings = 2,
            notes = 'Serve with lemon',
            nutrition_summary_json = '{"calories": 500}'
        WHERE id = ?
        """,
        (recipe_id,),
    )
    service.conn.commit()
    server = create_meals_server(service)

    result = _call(server, "recipe_detail", {"recipe_id": recipe_id})

    assert result["success"] is True
    assert result["data"]["recipe"]["title"] == "Salmon Dinner"
    assert result["data"]["recipe"]["source_url"] == "https://example.com/salmon"
    assert result["data"]["recipe"]["image_ref"] == "Assets/salmon.jpg"
    assert result["data"]["recipe"]["prep_time_minutes"] == 10
    assert result["data"]["recipe"]["cook_time_minutes"] == 20
    assert result["data"]["recipe"]["servings"] == 2
    assert result["data"]["recipe"]["notes"] == "Serve with lemon"
    assert result["data"]["recipe"]["nutrition_summary"] == {"calories": 500}
    assert result["data"]["recipe"]["ingredients"][0]["normalized_name"] == "salmon"


def test_launcher_server_manifest_includes_meals() -> None:
    from minx_mcp.launcher import SERVERS

    names = [server["name"] for server in SERVERS]
    assert names == ["minx-core", "minx-finance", "minx-meals"]
    assert all("module" in server for server in SERVERS)
