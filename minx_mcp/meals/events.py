from __future__ import annotations

from minx_mcp.event_payloads import EventPayload


class MealLoggedPayload(EventPayload):
    meal_id: int
    meal_kind: str
    food_count: int
    protein_grams: float | None = None
    calories: int | None = None


class NutritionDayUpdatedPayload(EventPayload):
    date: str
    meal_count: int
    protein_grams: float | None = None
    calories: int | None = None


class RecipeOrphanedPayload(EventPayload):
    recipe_id: int
    slug: str
    previous_vault_path: str
    reason: str


RECIPE_ORPHANED = "meals.recipe_orphaned"

MEALS_EVENT_PAYLOADS: dict[str, type[EventPayload]] = {
    "meal.logged": MealLoggedPayload,
    "nutrition.day_updated": NutritionDayUpdatedPayload,
    RECIPE_ORPHANED: RecipeOrphanedPayload,
}
