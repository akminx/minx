from __future__ import annotations

import json
import threading
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path
from sqlite3 import Connection, Row
from typing import Self
from zoneinfo import ZoneInfo

from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.events import emit_event
from minx_mcp.db import get_connection
from minx_mcp.meals.models import (
    MealEntry,
    NutritionPlan,
    NutritionProfile,
    NutritionTargets,
    PantryItem,
    Recipe,
    RecipeIngredient,
    RecipeSubstitution,
)
from minx_mcp.meals.nutrition import (
    ACTIVITY_MULTIPLIERS,
    SEX_BMR_OFFSETS,
    calculate_nutrition_targets,
)
from minx_mcp.meals.pantry import normalize_ingredient, pantry_item_from_row
from minx_mcp.meals.recipes import parse_recipe_note
from minx_mcp.preferences import get_preference
from minx_mcp.time_utils import format_utc_timestamp, normalize_utc_timestamp

EVENT_SOURCE = "meals.service"
VALID_MEAL_KINDS = {"breakfast", "lunch", "dinner", "snack", "other"}
VALID_ACTIVITY_LEVELS = set(ACTIVITY_MULTIPLIERS)
VALID_SEX_VALUES = set(SEX_BMR_OFFSETS)


class MealsService:
    def __init__(self, db_path: Path, vault_root: Path | None = None) -> None:
        self._db_path = db_path
        self._vault_root = vault_root
        self._local = threading.local()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def conn(self) -> Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = get_connection(self._db_path)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def log_meal(
        self,
        *,
        occurred_at: str,
        meal_kind: str,
        summary: str | None = None,
        food_items: list[dict[str, object]] | None = None,
        protein_grams: float | None = None,
        calories: int | None = None,
        carbs_grams: float | None = None,
        fat_grams: float | None = None,
        notes: str | None = None,
        source: str = "manual",
    ) -> MealEntry:
        if meal_kind not in VALID_MEAL_KINDS:
            raise InvalidInputError("meal_kind must be one of breakfast, lunch, dinner, snack, other")
        items = food_items or []
        savepoint = "meals_log_meal"
        self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            cursor = self.conn.execute(
                """
                INSERT INTO meals_meal_entries (
                    occurred_at, meal_kind, summary, food_items_json, protein_grams,
                    calories, carbs_grams, fat_grams, notes, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    occurred_at,
                    meal_kind,
                    summary,
                    json.dumps(items),
                    protein_grams,
                    calories,
                    carbs_grams,
                    fat_grams,
                    notes,
                    source,
                ),
            )
            meal_id = int(cursor.lastrowid or 0)
            event_id = emit_event(
                self.conn,
                event_type="meal.logged",
                domain="meals",
                occurred_at=occurred_at,
                entity_ref=f"meal-{meal_id}",
                source=EVENT_SOURCE,
                payload={
                    "meal_id": meal_id,
                    "meal_kind": meal_kind,
                    "food_count": len(items),
                    "protein_grams": protein_grams,
                    "calories": calories,
                },
            )
            if event_id is None:
                raise RuntimeError("meal.logged event emission failed")
            entry = self._get_meal_entry(meal_id)
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        self.conn.commit()
        return entry

    def add_pantry_item(
        self,
        *,
        display_name: str,
        quantity: float | None = None,
        unit: str | None = None,
        expiration_date: str | None = None,
        low_stock_threshold: float | None = None,
        source: str = "manual",
    ) -> PantryItem:
        cursor = self.conn.execute(
            """
            INSERT INTO meals_pantry_items (
                display_name, normalized_name, quantity, unit, expiration_date,
                low_stock_threshold, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                display_name,
                normalize_ingredient(display_name),
                quantity,
                unit,
                expiration_date,
                low_stock_threshold,
                source,
            ),
        )
        self.conn.commit()
        return self.get_pantry_item(cursor.lastrowid or 0)

    def update_pantry_item(
        self,
        item_id: int,
        *,
        quantity: float | None = None,
        unit: str | None = None,
        expiration_date: str | None = None,
        low_stock_threshold: float | None = None,
    ) -> PantryItem:
        self.get_pantry_item(item_id)
        self.conn.execute(
            """
            UPDATE meals_pantry_items
            SET quantity = COALESCE(?, quantity),
                unit = COALESCE(?, unit),
                expiration_date = COALESCE(?, expiration_date),
                low_stock_threshold = COALESCE(?, low_stock_threshold),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (quantity, unit, expiration_date, low_stock_threshold, item_id),
        )
        self.conn.commit()
        return self.get_pantry_item(item_id)

    def remove_pantry_item(self, item_id: int) -> None:
        self.get_pantry_item(item_id)
        self.conn.execute("DELETE FROM meals_pantry_items WHERE id = ?", (item_id,))
        self.conn.commit()

    def get_pantry_item(self, item_id: int) -> PantryItem:
        row = self.conn.execute(
            """
            SELECT id, display_name, normalized_name, quantity, unit, expiration_date,
                   low_stock_threshold, source
            FROM meals_pantry_items
            WHERE id = ?
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"pantry item {item_id} not found")
        return pantry_item_from_row(row)

    def list_pantry_items(self) -> list[PantryItem]:
        rows = self.conn.execute(
            """
            SELECT id, display_name, normalized_name, quantity, unit, expiration_date,
                   low_stock_threshold, source
            FROM meals_pantry_items
            ORDER BY normalized_name ASC, id ASC
            """
        ).fetchall()
        return [pantry_item_from_row(row) for row in rows]

    def list_meals(self, date: str | None = None) -> list[MealEntry]:
        rows = self.conn.execute(
            """
            SELECT id, occurred_at, meal_kind, summary, food_items_json, protein_grams,
                   calories, carbs_grams, fat_grams, notes, source
            FROM meals_meal_entries
            ORDER BY occurred_at ASC, id ASC
            """
        ).fetchall()
        entries = [_meal_entry_from_row(row) for row in rows]
        if date is None:
            return entries

        start_utc, end_utc = _local_day_utc_bounds(date, _resolve_timezone_name(self.conn))
        normalized = [
            (normalize_utc_timestamp(entry.occurred_at), entry)
            for entry in entries
        ]
        return [
            entry
            for occurred_at, entry in sorted(normalized, key=lambda item: (item[0], item[1].id))
            if start_utc <= occurred_at < end_utc
        ]

    def set_nutrition_profile(
        self,
        *,
        sex: str,
        age_years: int,
        height_cm: float,
        weight_kg: float,
        activity_level: str,
        goal: str = "fat_loss",
        calorie_deficit_kcal: int = 400,
        protein_g_per_kg: float = 2.0,
        fat_g_per_kg: float = 0.77,
        source: str = "manual",
    ) -> NutritionPlan:
        normalized_sex = sex.strip().lower()
        normalized_activity = activity_level.strip().lower()
        normalized_goal = goal.strip().lower() or "fat_loss"
        if normalized_sex not in VALID_SEX_VALUES:
            raise InvalidInputError("sex must be one of male, female, other")
        if age_years <= 0:
            raise InvalidInputError("age_years must be a positive integer")
        if height_cm <= 0:
            raise InvalidInputError("height_cm must be a positive number")
        if weight_kg <= 0:
            raise InvalidInputError("weight_kg must be a positive number")
        if normalized_activity not in VALID_ACTIVITY_LEVELS:
            levels = ", ".join(sorted(VALID_ACTIVITY_LEVELS))
            raise InvalidInputError(f"activity_level must be one of: {levels}")
        if calorie_deficit_kcal < 0:
            raise InvalidInputError("calorie_deficit_kcal must be zero or greater")
        if protein_g_per_kg <= 0:
            raise InvalidInputError("protein_g_per_kg must be positive")
        if fat_g_per_kg <= 0:
            raise InvalidInputError("fat_g_per_kg must be positive")

        calculated = calculate_nutrition_targets(
            sex=normalized_sex,
            age_years=age_years,
            height_cm=height_cm,
            weight_kg=weight_kg,
            activity_level=normalized_activity,
            calorie_deficit_kcal=calorie_deficit_kcal,
            protein_g_per_kg=protein_g_per_kg,
            fat_g_per_kg=fat_g_per_kg,
        )

        self.conn.execute(
            """
            INSERT INTO meals_nutrition_profiles (
                id, sex, age_years, height_cm, weight_kg, activity_level,
                goal, calorie_deficit_kcal, protein_g_per_kg, fat_g_per_kg,
                source
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                sex = excluded.sex,
                age_years = excluded.age_years,
                height_cm = excluded.height_cm,
                weight_kg = excluded.weight_kg,
                activity_level = excluded.activity_level,
                goal = excluded.goal,
                calorie_deficit_kcal = excluded.calorie_deficit_kcal,
                protein_g_per_kg = excluded.protein_g_per_kg,
                fat_g_per_kg = excluded.fat_g_per_kg,
                source = excluded.source,
                updated_at = datetime('now')
            """,
            (
                normalized_sex,
                age_years,
                height_cm,
                weight_kg,
                normalized_activity,
                normalized_goal,
                calorie_deficit_kcal,
                protein_g_per_kg,
                fat_g_per_kg,
                source,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO meals_nutrition_targets (
                profile_id, bmr_kcal, tdee_kcal, calorie_target_kcal,
                protein_target_grams, fat_target_grams, carbs_target_grams
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                bmr_kcal = excluded.bmr_kcal,
                tdee_kcal = excluded.tdee_kcal,
                calorie_target_kcal = excluded.calorie_target_kcal,
                protein_target_grams = excluded.protein_target_grams,
                fat_target_grams = excluded.fat_target_grams,
                carbs_target_grams = excluded.carbs_target_grams,
                updated_at = datetime('now')
            """,
            (
                1,
                calculated.bmr_kcal,
                calculated.tdee_kcal,
                calculated.calorie_target_kcal,
                calculated.protein_target_grams,
                calculated.fat_target_grams,
                calculated.carbs_target_grams,
            ),
        )
        self.conn.commit()

        plan = self.get_nutrition_plan()
        if plan is None:
            raise RuntimeError("nutrition profile persisted but could not be loaded")
        return plan

    def get_nutrition_profile(self) -> NutritionProfile | None:
        row = self.conn.execute(
            """
            SELECT id, sex, age_years, height_cm, weight_kg, activity_level,
                   goal, calorie_deficit_kcal, protein_g_per_kg, fat_g_per_kg,
                   source
            FROM meals_nutrition_profiles
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return None
        return _nutrition_profile_from_row(row)

    def get_nutrition_targets(self) -> NutritionTargets | None:
        row = self.conn.execute(
            """
            SELECT profile_id, bmr_kcal, tdee_kcal, calorie_target_kcal,
                   protein_target_grams, fat_target_grams, carbs_target_grams
            FROM meals_nutrition_targets
            WHERE profile_id = 1
            """
        ).fetchone()
        if row is None:
            return None
        return _nutrition_targets_from_row(row)

    def get_nutrition_plan(self) -> NutritionPlan | None:
        profile = self.get_nutrition_profile()
        targets = self.get_nutrition_targets()
        if profile is None or targets is None:
            return None
        return NutritionPlan(profile=profile, targets=targets)

    def index_recipe(self, relative_path: str) -> Recipe:
        if self._vault_root is None:
            raise InvalidInputError("vault_root is required to index recipes")
        path = _vault_child_path(self._vault_root, relative_path)
        vault_path = _vault_relative_path(self._vault_root, path)
        if not path.exists():
            raise NotFoundError(f"recipe note {relative_path} not found")
        parsed = parse_recipe_note(path)
        existing = self.conn.execute(
            "SELECT id, content_hash FROM meals_recipes WHERE vault_path = ?",
            (vault_path,),
        ).fetchone()
        if existing is not None and existing["content_hash"] == parsed.content_hash:
            return self.get_recipe(int(existing["id"]))
        if existing is None:
            cursor = self.conn.execute(
                """
                INSERT INTO meals_recipes (
                    vault_path, title, normalized_title, source_url, image_ref,
                    prep_time_minutes, cook_time_minutes, servings, tags_json,
                    notes, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vault_path,
                    parsed.title,
                    normalize_ingredient(parsed.title),
                    parsed.source_url,
                    parsed.image_ref,
                    parsed.prep_time_minutes,
                    parsed.cook_time_minutes,
                    parsed.servings,
                    json.dumps(parsed.tags),
                    parsed.notes,
                    parsed.content_hash,
                ),
            )
            recipe_id = cursor.lastrowid or 0
        else:
            recipe_id = int(existing["id"])
            self.conn.execute(
                """
                UPDATE meals_recipes
                SET title = ?, normalized_title = ?, source_url = ?, image_ref = ?,
                    prep_time_minutes = ?, cook_time_minutes = ?, servings = ?,
                    tags_json = ?, notes = ?, content_hash = ?,
                    indexed_at = datetime('now'), updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    parsed.title,
                    normalize_ingredient(parsed.title),
                    parsed.source_url,
                    parsed.image_ref,
                    parsed.prep_time_minutes,
                    parsed.cook_time_minutes,
                    parsed.servings,
                    json.dumps(parsed.tags),
                    parsed.notes,
                    parsed.content_hash,
                    recipe_id,
                ),
            )
            self.conn.execute("DELETE FROM meals_recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
        ingredient_ids_by_name: dict[str, int] = {}
        for ingredient in parsed.ingredients:
            cursor = self.conn.execute(
                """
                INSERT INTO meals_recipe_ingredients (
                    recipe_id, display_text, normalized_name, quantity, unit,
                    is_required, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recipe_id,
                    ingredient.display_text,
                    ingredient.normalized_name,
                    ingredient.quantity,
                    ingredient.unit,
                    int(ingredient.is_required),
                    ingredient.sort_order,
                ),
            )
            ingredient_ids_by_name.setdefault(ingredient.normalized_name, cursor.lastrowid or 0)
        for substitution in parsed.substitutions:
            ingredient_id = ingredient_ids_by_name.get(substitution.original_name)
            if ingredient_id is None:
                continue
            self.conn.execute(
                """
                INSERT INTO meals_recipe_substitutions (
                    recipe_ingredient_id, substitute_normalized_name,
                    display_text, priority
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    ingredient_id,
                    substitution.substitute_name,
                    substitution.display_text,
                    substitution.priority,
                ),
            )
        self.conn.commit()
        return self.get_recipe(recipe_id)

    def scan_vault_recipes(self, directory: str = "Recipes") -> list[Recipe]:
        if self._vault_root is None:
            raise InvalidInputError("vault_root is required to scan recipes")
        root = _vault_child_path(self._vault_root, directory)
        vault_root = self._vault_root.resolve()
        return [
            self.index_recipe(str(path.relative_to(vault_root)))
            for path in sorted(root.rglob("*.md"))
        ]

    def get_recipe(self, recipe_id: int) -> Recipe:
        row = self.conn.execute(
            """
            SELECT id, vault_path, title, normalized_title, source_url, image_ref,
                   prep_time_minutes, cook_time_minutes, servings, tags_json, notes,
                   nutrition_summary_json, content_hash
            FROM meals_recipes
            WHERE id = ?
            """,
            (recipe_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"recipe {recipe_id} not found")
        return _recipe_from_row(row, self._ingredients(recipe_id), self._substitutions(recipe_id))

    def list_recipes(self) -> list[Recipe]:
        rows = self.conn.execute(
            """
            SELECT id, vault_path, title, normalized_title, source_url, image_ref,
                   prep_time_minutes, cook_time_minutes, servings, tags_json, notes,
                   nutrition_summary_json, content_hash
            FROM meals_recipes
            ORDER BY title ASC, id ASC
            """
        ).fetchall()
        return [_recipe_from_row(row, self._ingredients(int(row["id"])), self._substitutions(int(row["id"]))) for row in rows]

    def _get_meal_entry(self, meal_id: int) -> MealEntry:
        row = self.conn.execute(
            """
            SELECT id, occurred_at, meal_kind, summary, food_items_json, protein_grams,
                   calories, carbs_grams, fat_grams, notes, source
            FROM meals_meal_entries
            WHERE id = ?
            """,
            (meal_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"meal {meal_id} not found")
        return _meal_entry_from_row(row)

    def _ingredients(self, recipe_id: int) -> list[RecipeIngredient]:
        rows = self.conn.execute(
            """
            SELECT id, recipe_id, display_text, normalized_name, quantity, unit,
                   is_required, ingredient_group, sort_order, notes
            FROM meals_recipe_ingredients
            WHERE recipe_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (recipe_id,),
        ).fetchall()
        return [_ingredient_from_row(row) for row in rows]

    def _substitutions(self, recipe_id: int) -> list[RecipeSubstitution]:
        rows = self.conn.execute(
            """
            SELECT s.id, s.recipe_ingredient_id, s.substitute_normalized_name,
                   s.display_text, s.quantity, s.unit, s.priority, s.notes
            FROM meals_recipe_substitutions s
            JOIN meals_recipe_ingredients i ON i.id = s.recipe_ingredient_id
            WHERE i.recipe_id = ?
            ORDER BY s.priority ASC, s.id ASC
            """,
            (recipe_id,),
        ).fetchall()
        return [_substitution_from_row(row) for row in rows]


def _vault_child_path(vault_root: Path, relative_path: str) -> Path:
    root = vault_root.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise InvalidInputError("recipe note path must be inside vault_root")
    return path


def _vault_relative_path(vault_root: Path, path: Path) -> str:
    return path.relative_to(vault_root.resolve()).as_posix()


def _meal_entry_from_row(row: Row) -> MealEntry:
    return MealEntry(
        id=int(row["id"]),
        occurred_at=str(row["occurred_at"]),
        meal_kind=str(row["meal_kind"]),
        summary=row["summary"],
        food_items=json.loads(row["food_items_json"]),
        protein_grams=float(row["protein_grams"]) if row["protein_grams"] is not None else None,
        calories=int(row["calories"]) if row["calories"] is not None else None,
        carbs_grams=float(row["carbs_grams"]) if row["carbs_grams"] is not None else None,
        fat_grams=float(row["fat_grams"]) if row["fat_grams"] is not None else None,
        notes=row["notes"],
        source=str(row["source"]),
    )


def _nutrition_profile_from_row(row: Row) -> NutritionProfile:
    return NutritionProfile(
        id=int(row["id"]),
        sex=str(row["sex"]),
        age_years=int(row["age_years"]),
        height_cm=float(row["height_cm"]),
        weight_kg=float(row["weight_kg"]),
        activity_level=str(row["activity_level"]),
        goal=str(row["goal"]),
        calorie_deficit_kcal=int(row["calorie_deficit_kcal"]),
        protein_g_per_kg=float(row["protein_g_per_kg"]),
        fat_g_per_kg=float(row["fat_g_per_kg"]),
        source=str(row["source"]),
    )


def _nutrition_targets_from_row(row: Row) -> NutritionTargets:
    return NutritionTargets(
        profile_id=int(row["profile_id"]),
        bmr_kcal=int(row["bmr_kcal"]),
        tdee_kcal=int(row["tdee_kcal"]),
        calorie_target_kcal=int(row["calorie_target_kcal"]),
        protein_target_grams=int(row["protein_target_grams"]),
        fat_target_grams=int(row["fat_target_grams"]),
        carbs_target_grams=int(row["carbs_target_grams"]),
    )


def _ingredient_from_row(row: Row) -> RecipeIngredient:
    return RecipeIngredient(
        id=int(row["id"]),
        recipe_id=int(row["recipe_id"]),
        display_text=str(row["display_text"]),
        normalized_name=str(row["normalized_name"]),
        quantity=float(row["quantity"]) if row["quantity"] is not None else None,
        unit=row["unit"],
        is_required=bool(row["is_required"]),
        ingredient_group=row["ingredient_group"],
        sort_order=int(row["sort_order"]),
        notes=row["notes"],
    )


def _substitution_from_row(row: Row) -> RecipeSubstitution:
    return RecipeSubstitution(
        id=int(row["id"]),
        recipe_ingredient_id=int(row["recipe_ingredient_id"]),
        substitute_normalized_name=str(row["substitute_normalized_name"]),
        display_text=str(row["display_text"]),
        quantity=float(row["quantity"]) if row["quantity"] is not None else None,
        unit=row["unit"],
        priority=int(row["priority"]),
        notes=row["notes"],
    )


def _recipe_from_row(
    row: Row,
    ingredients: list[RecipeIngredient],
    substitutions: list[RecipeSubstitution],
) -> Recipe:
    nutrition_summary = (
        json.loads(row["nutrition_summary_json"])
        if row["nutrition_summary_json"] is not None
        else None
    )
    return Recipe(
        id=int(row["id"]),
        vault_path=str(row["vault_path"]),
        title=str(row["title"]),
        normalized_title=str(row["normalized_title"]),
        source_url=row["source_url"],
        image_ref=row["image_ref"],
        prep_time_minutes=row["prep_time_minutes"],
        cook_time_minutes=row["cook_time_minutes"],
        servings=row["servings"],
        tags=json.loads(row["tags_json"]),
        notes=row["notes"],
        nutrition_summary=nutrition_summary,
        content_hash=str(row["content_hash"]),
        ingredients=ingredients,
        substitutions=substitutions,
    )


def _resolve_timezone_name(conn: Connection) -> str:
    configured = get_preference(conn, "core", "timezone", None)
    if isinstance(configured, str) and configured:
        return configured
    tzinfo = datetime.now().astimezone().tzinfo
    key = getattr(tzinfo, "key", None)
    return key if isinstance(key, str) and key else "UTC"


def _local_day_utc_bounds(review_date: str, timezone_name: str) -> tuple[str, str]:
    zone = ZoneInfo(timezone_name)
    local_day = date_cls.fromisoformat(review_date)
    local_start = datetime.combine(local_day, datetime.min.time(), tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    return format_utc_timestamp(local_start), format_utc_timestamp(local_end)
