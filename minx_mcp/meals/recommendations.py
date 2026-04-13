from __future__ import annotations

from datetime import date
from pathlib import Path
from sqlite3 import Connection

from minx_mcp.meals.models import (
    ClassificationResult,
    RecommendationResult,
    Recipe,
    RecipeMetadata,
    RecipeRecommendation,
)
from minx_mcp.meals.pantry import get_expiring_items, get_low_stock_items
from minx_mcp.meals.service import MealsService

_CLASS_ORDER = {"make_now": 0, "make_with_substitutions": 1, "needs_shopping": 2, "excluded": 3}


def classify_recipe(
    *,
    required_names: list[str],
    optional_names: list[str],
    pantry_names: set[str],
    substitution_map: dict[str, list[str]],
) -> ClassificationResult:
    del optional_names
    if not required_names:
        return ClassificationResult(
            availability_class="excluded",
            pantry_coverage_ratio=0.0,
            missing_required_count=0,
            substitution_count=0,
            matched_ingredients=[],
            substitutions=[],
            missing_required_ingredients=[],
            reasons=["recipe has no required ingredients"],
        )

    matched: list[str] = []
    substitutions: list[dict[str, str]] = []
    missing: list[str] = []
    for name in required_names:
        if name in pantry_names:
            matched.append(name)
            continue
        substitute = next((sub for sub in substitution_map.get(name, []) if sub in pantry_names), None)
        if substitute is not None:
            matched.append(name)
            substitutions.append({"ingredient": name, "substitute": substitute})
            continue
        missing.append(name)

    if missing:
        availability_class = "needs_shopping"
    elif substitutions:
        availability_class = "make_with_substitutions"
    else:
        availability_class = "make_now"

    return ClassificationResult(
        availability_class=availability_class,
        pantry_coverage_ratio=len(matched) / len(required_names),
        missing_required_count=len(missing),
        substitution_count=len(substitutions),
        matched_ingredients=matched,
        substitutions=substitutions,
        missing_required_ingredients=missing,
        reasons=_classification_reasons(availability_class, missing, substitutions),
    )


def rank_recommendations(recs: list[RecipeRecommendation]) -> list[RecipeRecommendation]:
    return sorted(
        recs,
        key=lambda rec: (
            _CLASS_ORDER.get(rec.availability_class, 99),
            -rec.expiring_ingredient_hits,
            -rec.low_stock_ingredient_hits,
            -rec.pantry_coverage_ratio,
            rec.missing_required_count,
            rec.substitution_count,
            rec.recipe_title,
        ),
    )


def recommend_recipes(
    conn: Connection,
    *,
    as_of: str | None = None,
    include_needs_shopping: bool = False,
) -> RecommendationResult:
    service = MealsService.__new__(MealsService)
    service._db_path = Path(".")
    service._vault_root = None
    service._local = type("_Local", (), {"conn": conn})()
    recipes = service.list_recipes()
    pantry_items = service.list_pantry_items()
    pantry_names = {item.normalized_name for item in pantry_items}
    day = as_of or date.today().isoformat()
    expiring_names = {item.normalized_name for item in get_expiring_items(conn, day)}
    low_stock_names = {item.normalized_name for item in get_low_stock_items(conn)}

    recommendations = rank_recommendations(
        [_recommendation_for_recipe(recipe, pantry_names, expiring_names, low_stock_names) for recipe in recipes]
    )
    base_classes = {"make_now", "make_with_substitutions"}
    has_base = any(rec.availability_class in base_classes for rec in recommendations)
    included = set(base_classes)
    if include_needs_shopping or not has_base:
        included.add("needs_shopping")
    filtered = [
        rec for rec in recommendations if rec.availability_class in included and rec.availability_class != "excluded"
    ]
    return RecommendationResult(
        recommendations=filtered,
        included_classes=[cls for cls in ("make_now", "make_with_substitutions", "needs_shopping") if cls in included],
        shopping_lists_generated=[],
    )


def _recommendation_for_recipe(
    recipe: Recipe,
    pantry_names: set[str],
    expiring_names: set[str],
    low_stock_names: set[str],
) -> RecipeRecommendation:
    required = [ingredient.normalized_name for ingredient in recipe.ingredients if ingredient.is_required]
    optional = [ingredient.normalized_name for ingredient in recipe.ingredients if not ingredient.is_required]
    by_id = {ingredient.id: ingredient.normalized_name for ingredient in recipe.ingredients}
    substitution_map: dict[str, list[str]] = {}
    for substitution in recipe.substitutions:
        original = by_id.get(substitution.recipe_ingredient_id)
        if original is None:
            continue
        substitution_map.setdefault(original, []).append(substitution.substitute_normalized_name)
    classification = classify_recipe(
        required_names=required,
        optional_names=optional,
        pantry_names=pantry_names,
        substitution_map=substitution_map,
    )
    ingredient_names = set(required + optional)
    reasons = list(classification.reasons)
    expiring_hits = len(ingredient_names & expiring_names)
    low_stock_hits = len(ingredient_names & low_stock_names)
    if expiring_hits:
        reasons.append("uses ingredients expiring soon")
    if low_stock_hits:
        reasons.append("uses low-stock ingredients")
    return RecipeRecommendation(
        recipe_id=recipe.id,
        recipe_title=recipe.title,
        availability_class=classification.availability_class,
        pantry_coverage_ratio=classification.pantry_coverage_ratio,
        expiring_ingredient_hits=expiring_hits,
        low_stock_ingredient_hits=low_stock_hits,
        missing_required_count=classification.missing_required_count,
        substitution_count=classification.substitution_count,
        matched_ingredients=classification.matched_ingredients,
        substitutions=classification.substitutions,
        missing_required_ingredients=classification.missing_required_ingredients,
        reasons=reasons,
        recipe=RecipeMetadata(
            vault_path=recipe.vault_path,
            image_ref=recipe.image_ref,
            tags=recipe.tags,
            source_url=recipe.source_url,
            prep_time_minutes=recipe.prep_time_minutes,
            cook_time_minutes=recipe.cook_time_minutes,
            servings=recipe.servings,
            notes=recipe.notes,
            nutrition_summary=recipe.nutrition_summary,
        ),
    )


def _classification_reasons(
    availability_class: str,
    missing: list[str],
    substitutions: list[dict[str, str]],
) -> list[str]:
    if availability_class == "make_now":
        return ["all required ingredients are in pantry"]
    if availability_class == "make_with_substitutions":
        return [f"uses {sub['substitute']} for {sub['ingredient']}" for sub in substitutions]
    if availability_class == "needs_shopping":
        return [f"missing {name}" for name in missing]
    return ["recipe is excluded"]
