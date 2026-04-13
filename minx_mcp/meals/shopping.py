from __future__ import annotations

from dataclasses import dataclass

from minx_mcp.meals.models import PantryItem, Recipe, RecipeIngredient


@dataclass(frozen=True)
class ShoppingItemDraft:
    ingredient: RecipeIngredient
    pantry_quantity: float | None
    missing_quantity: float | None
    pantry_unit: str | None
    notes: str | None


@dataclass(frozen=True)
class _Coverage:
    covered: bool
    missing_quantity: float | None
    pantry_quantity: float | None
    pantry_unit: str | None
    notes: str | None


def missing_shopping_items(recipe: Recipe, pantry_items: list[PantryItem]) -> list[ShoppingItemDraft]:
    pantry_by_name = _pantry_by_name(pantry_items)
    substitution_map = _substitution_map(recipe)
    drafts: list[ShoppingItemDraft] = []
    for ingredient in recipe.ingredients:
        if not ingredient.is_required:
            continue
        direct_items = pantry_by_name.get(ingredient.normalized_name, [])
        direct_coverage = _coverage(ingredient, direct_items)
        if direct_coverage.covered:
            continue
        if _has_covering_substitution(ingredient, substitution_map.get(ingredient.id, []), pantry_by_name):
            continue
        drafts.append(
            ShoppingItemDraft(
                ingredient=ingredient,
                pantry_quantity=direct_coverage.pantry_quantity,
                missing_quantity=direct_coverage.missing_quantity,
                pantry_unit=direct_coverage.pantry_unit,
                notes=direct_coverage.notes,
            )
        )
    return drafts


def _coverage(ingredient: RecipeIngredient, pantry_items: list[PantryItem]) -> _Coverage:
    if not pantry_items:
        return _Coverage(
            covered=False,
            missing_quantity=ingredient.quantity,
            pantry_quantity=None,
            pantry_unit=None,
            notes="not in pantry",
        )
    if ingredient.quantity is None:
        return _Coverage(
            covered=True,
            missing_quantity=None,
            pantry_quantity=None,
            pantry_unit=None,
            notes=None,
        )
    total_quantity = 0.0
    pantry_unit: str | None = None
    for pantry_item in pantry_items:
        if pantry_item.quantity is None:
            return _Coverage(
                covered=True,
                missing_quantity=None,
                pantry_quantity=None,
                pantry_unit=pantry_item.unit,
                notes="pantry quantity unknown",
            )
        if not _same_unit(ingredient.unit, pantry_item.unit):
            return _Coverage(
                covered=True,
                missing_quantity=None,
                pantry_quantity=None,
                pantry_unit=pantry_item.unit,
                notes="pantry unit not comparable",
            )
        total_quantity += pantry_item.quantity
        pantry_unit = pantry_item.unit

    missing = ingredient.quantity - total_quantity
    if missing <= 0:
        return _Coverage(
            covered=True,
            missing_quantity=None,
            pantry_quantity=total_quantity,
            pantry_unit=pantry_unit,
            notes=None,
        )
    return _Coverage(
        covered=False,
        missing_quantity=missing,
        pantry_quantity=total_quantity,
        pantry_unit=pantry_unit,
        notes="pantry quantity below recipe quantity",
    )


def _has_covering_substitution(
    ingredient: RecipeIngredient,
    substitution_names: list[str],
    pantry_by_name: dict[str, list[PantryItem]],
) -> bool:
    for substitute_name in substitution_names:
        substitute_items = pantry_by_name.get(substitute_name, [])
        if _coverage(ingredient, substitute_items).covered:
            return True
    return False


def _substitution_map(recipe: Recipe) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for substitution in sorted(recipe.substitutions, key=lambda sub: (sub.priority, sub.id)):
        result.setdefault(substitution.recipe_ingredient_id, []).append(
            substitution.substitute_normalized_name
        )
    return result


def _pantry_by_name(pantry_items: list[PantryItem]) -> dict[str, list[PantryItem]]:
    pantry: dict[str, list[PantryItem]] = {}
    for item in pantry_items:
        pantry.setdefault(item.normalized_name, []).append(item)
    return pantry


def _same_unit(left: str | None, right: str | None) -> bool:
    return (left or "").strip().lower() == (right or "").strip().lower()
