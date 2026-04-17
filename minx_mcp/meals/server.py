from __future__ import annotations

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import ToolResponse, wrap_tool_call
from minx_mcp.meals.recommendations import recommend_recipes as recommend
from minx_mcp.meals.service import MealsService


def create_meals_server(service: MealsService) -> FastMCP:
    mcp = FastMCP("minx-meals", stateless_http=True, json_response=True)

    @mcp.tool(name="meal_log")
    def meal_log(
        meal_kind: str,
        occurred_at: str,
        summary: str | None = None,
        food_items: list[dict[str, object]] | None = None,
        protein_grams: float | None = None,
        calories: int | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "meal": asdict(
                    service.log_meal(
                        meal_kind=meal_kind,
                        occurred_at=occurred_at,
                        summary=summary,
                        food_items=food_items,
                        protein_grams=protein_grams,
                        calories=calories,
                    )
                )
            }
        )

    @mcp.tool(name="pantry_add")
    def pantry_add(
        display_name: str,
        quantity: float | None = None,
        unit: str | None = None,
        expiration_date: str | None = None,
        low_stock_threshold: float | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "item": asdict(
                    service.add_pantry_item(
                        display_name=display_name,
                        quantity=quantity,
                        unit=unit,
                        expiration_date=expiration_date,
                        low_stock_threshold=low_stock_threshold,
                    )
                )
            }
        )

    @mcp.tool(name="pantry_update")
    def pantry_update(
        item_id: int,
        quantity: float | None = None,
        unit: str | None = None,
        expiration_date: str | None = None,
        low_stock_threshold: float | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "item": asdict(
                    service.update_pantry_item(
                        item_id,
                        quantity=quantity,
                        unit=unit,
                        expiration_date=expiration_date,
                        low_stock_threshold=low_stock_threshold,
                    )
                )
            }
        )

    @mcp.tool(name="pantry_remove")
    def pantry_remove(item_id: int) -> ToolResponse:
        return wrap_tool_call(lambda: _remove_pantry_item(service, item_id))

    @mcp.tool(name="pantry_list")
    def pantry_list() -> ToolResponse:
        return wrap_tool_call(
            lambda: {"items": [asdict(item) for item in service.list_pantry_items()]}
        )

    @mcp.tool(name="recipe_index")
    def recipe_index(vault_path: str) -> ToolResponse:
        return wrap_tool_call(lambda: {"recipe": asdict(service.index_recipe(vault_path))})

    @mcp.tool(name="recipe_scan")
    def recipe_scan(directory: str = "Recipes") -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "recipes": [asdict(recipe) for recipe in service.scan_vault_recipes(directory)]
            }
        )

    @mcp.tool(name="recommend_recipes")
    def recommend_recipes(
        include_needs_shopping: bool = False,
        apply_nutrition_filter: bool = False,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: asdict(
                recommend(
                    service.conn,
                    include_needs_shopping=include_needs_shopping,
                    apply_nutrition_filter=apply_nutrition_filter,
                )
            )
        )

    @mcp.tool(name="nutrition_profile_set")
    def nutrition_profile_set(
        sex: str,
        age_years: int,
        height_cm: float,
        weight_kg: float,
        activity_level: str,
        goal: str = "fat_loss",
        calorie_deficit_kcal: int = 400,
        protein_g_per_kg: float = 2.0,
        fat_g_per_kg: float = 0.77,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "plan": asdict(
                    service.set_nutrition_profile(
                        sex=sex,
                        age_years=age_years,
                        height_cm=height_cm,
                        weight_kg=weight_kg,
                        activity_level=activity_level,
                        goal=goal,
                        calorie_deficit_kcal=calorie_deficit_kcal,
                        protein_g_per_kg=protein_g_per_kg,
                        fat_g_per_kg=fat_g_per_kg,
                    )
                )
            }
        )

    @mcp.tool(name="nutrition_profile_get")
    def nutrition_profile_get() -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "plan": asdict(plan) if (plan := service.get_nutrition_plan()) is not None else None
            }
        )

    @mcp.resource("health://status")
    def health_status() -> str:
        import json

        return json.dumps({"status": "ok", "server": "minx-meals"})

    return mcp


def _remove_pantry_item(service: MealsService, item_id: int) -> dict[str, bool]:
    service.remove_pantry_item(item_id)
    return {"removed": True}
