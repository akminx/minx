from __future__ import annotations

from dataclasses import asdict

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import ToolResponse, wrap_tool_call
from minx_mcp.meals.recommendations import recommend_recipes as recommend
from minx_mcp.meals.service import MealsService
from minx_mcp.meals.templates import read_recipe_starter_template, recipe_starter_template_path
from minx_mcp.transport import health_payload
from minx_mcp.validation import require_non_empty, validate_iso_date, validate_iso_datetime


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
            lambda: _meal_log(
                service,
                meal_kind=meal_kind,
                occurred_at=occurred_at,
                summary=summary,
                food_items=food_items,
                protein_grams=protein_grams,
                calories=calories,
            ),
            tool_name="meal_log",
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
            lambda: _pantry_add(
                service,
                display_name=display_name,
                quantity=quantity,
                unit=unit,
                expiration_date=expiration_date,
                low_stock_threshold=low_stock_threshold,
            ),
            tool_name="pantry_add",
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
            },
            tool_name="pantry_update",
        )

    @mcp.tool(name="pantry_remove")
    def pantry_remove(item_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _remove_pantry_item(service, item_id),
            tool_name="pantry_remove",
        )

    @mcp.tool(name="pantry_list")
    def pantry_list() -> ToolResponse:
        return wrap_tool_call(
            lambda: {"items": [asdict(item) for item in service.list_pantry_items()]},
            tool_name="pantry_list",
        )

    @mcp.tool(name="recipe_index")
    def recipe_index(vault_path: str) -> ToolResponse:
        return wrap_tool_call(
            lambda: {"recipe": asdict(service.index_recipe(vault_path))},
            tool_name="recipe_index",
        )

    @mcp.tool(name="recipe_scan")
    def recipe_scan(directory: str = "Recipes") -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "recipes": [asdict(recipe) for recipe in service.scan_vault_recipes(directory)]
            },
            tool_name="recipe_scan",
        )

    @mcp.tool(name="recipes_reconcile")
    def recipes_reconcile() -> ToolResponse:
        """Walk vault-backed recipes and orphan rows whose files no longer exist in the vault."""

        def _run() -> dict[str, object]:
            result = service.reconcile_vault_recipes()
            return {
                "checked": result.checked,
                "orphaned": result.orphaned,
                "orphaned_recipe_ids": result.orphaned_recipe_ids,
            }

        return wrap_tool_call(_run, tool_name="recipes_reconcile")

    @mcp.tool(name="recipe_template")
    def recipe_template() -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "filename": recipe_starter_template_path().name,
                "template": read_recipe_starter_template(),
            },
            tool_name="recipe_template",
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
            ),
            tool_name="recommend_recipes",
        )

    @mcp.tool(name="nutrition_profile_set")
    def nutrition_profile_set(
        sex: str,
        age_years: int,
        height_cm: float,
        weight_kg: float,
        activity_level: str,
        goal: str = "maintenance",
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
            },
            tool_name="nutrition_profile_set",
        )

    @mcp.tool(name="nutrition_profile_get")
    def nutrition_profile_get() -> ToolResponse:
        return wrap_tool_call(
            lambda: {
                "plan": asdict(plan) if (plan := service.get_nutrition_plan()) is not None else None
            },
            tool_name="nutrition_profile_get",
        )

    @mcp.resource("health://status")
    def health_status() -> str:
        return health_payload("minx-meals")

    return mcp


def _remove_pantry_item(service: MealsService, item_id: int) -> dict[str, bool]:
    service.remove_pantry_item(item_id)
    return {"removed": True}


def _meal_log(
    service: MealsService,
    *,
    meal_kind: str,
    occurred_at: str,
    summary: str | None,
    food_items: list[dict[str, object]] | None,
    protein_grams: float | None,
    calories: int | None,
) -> dict[str, object]:
    validate_iso_datetime(occurred_at, field_name="occurred_at")
    return {
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


def _pantry_add(
    service: MealsService,
    *,
    display_name: str,
    quantity: float | None,
    unit: str | None,
    expiration_date: str | None,
    low_stock_threshold: float | None,
) -> dict[str, object]:
    name = require_non_empty("display_name", display_name).strip()
    if expiration_date is not None:
        validate_iso_date(expiration_date, field_name="expiration_date")
    return {
        "item": asdict(
            service.add_pantry_item(
                display_name=name,
                quantity=quantity,
                unit=unit,
                expiration_date=expiration_date,
                low_stock_threshold=low_stock_threshold,
            )
        )
    }
