from __future__ import annotations

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import wrap_tool_call
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
    ) -> dict[str, object]:
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
    ) -> dict[str, object]:
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
    ) -> dict[str, object]:
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
    def pantry_remove(item_id: int) -> dict[str, object]:
        return wrap_tool_call(lambda: _remove_pantry_item(service, item_id))

    @mcp.tool(name="pantry_list")
    def pantry_list() -> dict[str, object]:
        return wrap_tool_call(
            lambda: {"items": [asdict(item) for item in service.list_pantry_items()]}
        )

    @mcp.tool(name="recipe_index")
    def recipe_index(vault_path: str) -> dict[str, object]:
        return wrap_tool_call(lambda: {"recipe": asdict(service.index_recipe(vault_path))})

    @mcp.tool(name="recipe_scan")
    def recipe_scan(directory: str = "Recipes") -> dict[str, object]:
        return wrap_tool_call(
            lambda: {
                "recipes": [
                    asdict(recipe) for recipe in service.scan_vault_recipes(directory)
                ]
            }
        )

    @mcp.tool(name="recipe_detail")
    def recipe_detail(recipe_id: int) -> dict[str, object]:
        return wrap_tool_call(lambda: {"recipe": asdict(service.get_recipe(recipe_id))})

    @mcp.tool(name="recommend_recipes")
    def recommend_recipes(include_needs_shopping: bool = False) -> dict[str, object]:
        return wrap_tool_call(
            lambda: asdict(
                recommend(service.conn, include_needs_shopping=include_needs_shopping)
            )
        )

    @mcp.tool(name="shopping_list_generate")
    def shopping_list_generate(recipe_id: int) -> dict[str, object]:
        return wrap_tool_call(
            lambda: {"shopping_list": asdict(service.generate_shopping_list(recipe_id))}
        )

    return mcp


def _remove_pantry_item(service: MealsService, item_id: int) -> dict[str, bool]:
    service.remove_pantry_item(item_id)
    return {"removed": True}
