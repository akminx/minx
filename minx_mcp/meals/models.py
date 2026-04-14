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
class NutritionProfile:
    id: int
    sex: str
    age_years: int
    height_cm: float
    weight_kg: float
    activity_level: str
    goal: str
    calorie_deficit_kcal: int
    protein_g_per_kg: float
    fat_g_per_kg: float
    source: str


@dataclass(frozen=True)
class NutritionTargets:
    profile_id: int
    bmr_kcal: int
    tdee_kcal: int
    calorie_target_kcal: int
    protein_target_grams: int
    fat_target_grams: int
    carbs_target_grams: int


@dataclass(frozen=True)
class NutritionPlan:
    profile: NutritionProfile
    targets: NutritionTargets


@dataclass(frozen=True)
class RecommendationNutritionContext:
    date: str
    calorie_target_kcal: int
    protein_target_grams: int
    consumed_calories_kcal: int | None
    consumed_protein_grams: float | None
    remaining_calorie_budget_kcal: int | None
    remaining_protein_target_grams: float | None


@dataclass(frozen=True)
class RecipeNutritionFit:
    calories_per_serving: int | None
    protein_grams_per_serving: float | None
    fits_remaining_calories: bool | None
    supports_remaining_protein: bool | None
    reasons: list[str]


@dataclass(frozen=True)
class RecipeMetadata:
    vault_path: str
    image_ref: str | None
    tags: list[str]
    source_url: str | None
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    servings: int | None = None
    notes: str | None = None
    nutrition_summary: dict[str, object] | None = None


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
    nutrition_fit: RecipeNutritionFit | None = None


@dataclass(frozen=True)
class RecommendationResult:
    recommendations: list[RecipeRecommendation]
    included_classes: list[str]
    shopping_lists_generated: list[object] = field(default_factory=list)
    nutrition_context: RecommendationNutritionContext | None = None
