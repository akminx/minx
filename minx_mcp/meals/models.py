from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MealEntry:
    id: int
    occurred_at: str
    meal_kind: str
    summary: str | None
    food_items: list[dict[str, object]]
    protein_grams: float | None
    calories: int | None
    carbs_grams: float | None
    fat_grams: float | None
    notes: str | None
    source: str


@dataclass(frozen=True)
class PantryItem:
    id: int
    display_name: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    expiration_date: str | None
    low_stock_threshold: float | None
    source: str


@dataclass(frozen=True)
class RecipeIngredient:
    id: int
    recipe_id: int
    display_text: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    is_required: bool
    ingredient_group: str | None
    sort_order: int
    notes: str | None


@dataclass(frozen=True)
class RecipeSubstitution:
    id: int
    recipe_ingredient_id: int
    substitute_normalized_name: str
    display_text: str
    quantity: float | None
    unit: str | None
    priority: int
    notes: str | None


@dataclass(frozen=True)
class Recipe:
    id: int
    vault_path: str
    title: str
    normalized_title: str
    source_url: str | None
    image_ref: str | None
    prep_time_minutes: int | None
    cook_time_minutes: int | None
    servings: int | None
    tags: list[str]
    notes: str | None
    nutrition_summary: dict[str, object] | None
    content_hash: str
    ingredients: list[RecipeIngredient] = field(default_factory=list)
    substitutions: list[RecipeSubstitution] = field(default_factory=list)


@dataclass(frozen=True)
class ClassificationResult:
    availability_class: str
    pantry_coverage_ratio: float
    missing_required_count: int
    substitution_count: int
    matched_ingredients: list[str]
    substitutions: list[dict[str, str]]
    missing_required_ingredients: list[str]
    reasons: list[str]


@dataclass(frozen=True)
class RecipeMetadata:
    vault_path: str
    image_ref: str | None
    tags: list[str]
    source_url: str | None
    prep_time_minutes: int | None
    cook_time_minutes: int | None
    servings: int | None
    notes: str | None
    nutrition_summary: dict[str, object] | None


@dataclass(frozen=True)
class RecipeRecommendation:
    recipe_id: int
    recipe_title: str
    availability_class: str
    pantry_coverage_ratio: float
    expiring_ingredient_hits: int
    low_stock_ingredient_hits: int
    missing_required_count: int
    substitution_count: int
    matched_ingredients: list[str]
    substitutions: list[dict[str, str]]
    missing_required_ingredients: list[str]
    reasons: list[str]
    recipe: RecipeMetadata


@dataclass(frozen=True)
class RecommendationResult:
    recommendations: list[RecipeRecommendation]
    included_classes: list[str]
    shopping_lists_generated: list[object] = field(default_factory=list)


@dataclass(frozen=True)
class ShoppingListItem:
    id: int
    shopping_list_id: int
    recipe_ingredient_id: int | None
    display_text: str
    normalized_name: str
    quantity: float | None
    unit: str | None
    pantry_quantity: float | None
    missing_quantity: float | None
    pantry_unit: str | None
    notes: str | None
    sort_order: int


@dataclass(frozen=True)
class ShoppingList:
    id: int
    recipe_id: int
    recipe_title: str
    title: str
    vault_path: str | None
    status: str
    created_at: str
    items: list[ShoppingListItem] = field(default_factory=list)
