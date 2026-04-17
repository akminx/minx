from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection, Row

from minx_mcp.base_service import BaseService
from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.events import emit_event
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
    GOAL_CALORIE_MULTIPLIERS,
    SEX_BMR_OFFSETS,
    calculate_nutrition_targets,
)
from minx_mcp.meals.pantry import normalize_ingredient, pantry_item_from_row
from minx_mcp.meals.recipes import parse_recipe_note
from minx_mcp.time_utils import (
    local_calendar_date_for_utc_timestamp,
    local_day_utc_bounds,
    resolve_timezone_name,
    utc_now_isoformat,
)

EVENT_SOURCE = "meals.service"
VALID_MEAL_KINDS = {"breakfast", "lunch", "dinner", "snack", "other"}
VALID_ACTIVITY_LEVELS = set(ACTIVITY_MULTIPLIERS)
VALID_SEX_VALUES = set(SEX_BMR_OFFSETS)
# Canonical nutrition goals for target calculation (see ``GOAL_CALORIE_MULTIPLIERS``).
# ``fat_loss`` / ``muscle_gain`` are accepted legacy aliases for ``cut`` / ``bulk``.
_GOAL_ALIASES: dict[str, str] = {"fat_loss": "cut", "muscle_gain": "bulk"}
_VALID_GOAL_CANONICAL = set(GOAL_CALORIE_MULTIPLIERS)


@dataclass(frozen=True)
class ReconcileRecipesResult:
    checked: int
    orphaned: int
    orphaned_recipe_ids: list[int]


def _vault_sync_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class MealsService(BaseService):
    def __init__(self, db_path: Path, vault_root: Path | None = None) -> None:
        super().__init__(db_path)
        self._vault_root = vault_root

    @classmethod
    def from_connection(cls, conn: Connection) -> MealsService:
        """Create an instance with an injected connection. Does NOT open a new connection."""
        instance = cls.__new__(cls)
        instance._db_path = Path(".")
        instance._vault_root = None
        instance._local = threading.local()
        instance._local.conn = conn
        return instance

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
            raise InvalidInputError(
                f"invalid meal_kind {meal_kind!r}; must be one of breakfast, lunch, dinner, snack, other"
            )
        items = food_items or []
        savepoint = f"log_meal_{secrets.token_hex(4)}"
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
            _emit_nutrition_day_updated(self.conn, occurred_at=occurred_at)
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
        if date is None:
            rows = self.conn.execute(
                """
                SELECT id, occurred_at, meal_kind, summary, food_items_json, protein_grams,
                       calories, carbs_grams, fat_grams, notes, source
                FROM meals_meal_entries
                ORDER BY occurred_at ASC, id ASC
                """
            ).fetchall()
            return [_meal_entry_from_row(row) for row in rows]

        start_utc, end_utc = local_day_utc_bounds(date, resolve_timezone_name(self.conn))
        rows = self.conn.execute(
            """
            SELECT id, occurred_at, meal_kind, summary, food_items_json, protein_grams,
                   calories, carbs_grams, fat_grams, notes, source
            FROM meals_meal_entries
            WHERE datetime(occurred_at) >= datetime(?)
              AND datetime(occurred_at) < datetime(?)
            ORDER BY occurred_at ASC, id ASC
            """,
            (start_utc, end_utc),
        ).fetchall()
        return [_meal_entry_from_row(row) for row in rows]

    def set_nutrition_profile(
        self,
        *,
        sex: str,
        age_years: int,
        height_cm: float,
        weight_kg: float,
        activity_level: str,
        goal: str = "maintenance",
        calorie_deficit_kcal: int = 400,
        protein_g_per_kg: float = 2.0,
        fat_g_per_kg: float = 0.77,
        source: str = "manual",
    ) -> NutritionPlan:
        normalized_sex = sex.strip().lower()
        normalized_activity = activity_level.strip().lower()
        normalized_goal = goal.strip().lower() or "maintenance"
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

        canonical_goal = _GOAL_ALIASES.get(normalized_goal, normalized_goal)
        if canonical_goal not in _VALID_GOAL_CANONICAL:
            allowed = ", ".join(sorted(_VALID_GOAL_CANONICAL | set(_GOAL_ALIASES)))
            raise InvalidInputError(f"goal must be one of: {allowed}")

        calculated = calculate_nutrition_targets(
            sex=normalized_sex,
            age_years=age_years,
            height_cm=height_cm,
            weight_kg=weight_kg,
            activity_level=normalized_activity,
            goal=canonical_goal,
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
            now_iso = _vault_sync_now_iso()
            self.conn.execute(
                "UPDATE meals_recipes SET vault_synced_at = ? WHERE id = ?",
                (now_iso, int(existing["id"])),
            )
            self.conn.commit()
            return self.get_recipe(int(existing["id"]))
        nutrition_summary_json = (
            json.dumps(parsed.nutrition_summary) if parsed.nutrition_summary is not None else None
        )
        now_iso = _vault_sync_now_iso()
        savepoint = "meals_index_recipe"
        self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            if existing is None:
                cursor = self.conn.execute(
                    """
                    INSERT INTO meals_recipes (
                        vault_path, title, normalized_title, source_url, image_ref,
                        prep_time_minutes, cook_time_minutes, servings, tags_json,
                        notes, nutrition_summary_json, content_hash, vault_synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        nutrition_summary_json,
                        parsed.content_hash,
                        now_iso,
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
                        tags_json = ?, notes = ?, nutrition_summary_json = ?, content_hash = ?,
                        indexed_at = datetime('now'), updated_at = datetime('now'),
                        vault_synced_at = ?
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
                        nutrition_summary_json,
                        parsed.content_hash,
                        now_iso,
                        recipe_id,
                    ),
                )
                self.conn.execute(
                    "DELETE FROM meals_recipe_ingredients WHERE recipe_id = ?", (recipe_id,)
                )
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
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        self.conn.commit()
        return self.get_recipe(recipe_id)

    def scan_vault_recipes(self, directory: str = "Recipes") -> list[Recipe]:
        if self._vault_root is None:
            raise InvalidInputError("vault_root is required to scan recipes")
        # Reconcile in its own transaction boundary BEFORE entering the
        # index_recipe loop. Previously the inner call shared the outer
        # transaction with subsequent index_recipe SAVEPOINTs; a
        # ROLLBACK TO SAVEPOINT in index_recipe does NOT undo statements
        # issued before the savepoint, so on mid-loop failure we could leave
        # reconcile writes (UPDATE meals_recipes + meals.recipe_orphaned
        # events) in an uncommitted outer transaction with no guaranteed
        # commit/rollback at this method's scope.
        self.reconcile_vault_recipes()
        root = _vault_child_path(self._vault_root, directory)
        vault_root = self._vault_root.resolve()
        paths = sorted(root.rglob("*.md"))
        recipes = [self.index_recipe(str(path.relative_to(vault_root))) for path in paths]
        if not paths:
            self.conn.commit()
        return recipes

    def reconcile_vault_recipes(self, vault_root: Path | None = None) -> ReconcileRecipesResult:
        """Walk meals_recipes rows; for each, verify vault_path still exists.

        If a file is missing, nullify vault_path (soft delete — preserve ingredient
        history and recipe id references from meal logs) and emit a
        'meals.recipe_orphaned' domain event. Returns a structured result with
        counts and orphaned recipe ids so callers can surface to the user.

        Returns both 'checked' (total rows walked) and 'orphaned' (count of newly
        nullified). Idempotent: re-running immediately produces zero orphans since
        already-nullified rows are skipped.
        """
        root = vault_root if vault_root is not None else self._vault_root
        if root is None:
            raise InvalidInputError("vault_root is required to reconcile vault recipes")
        savepoint = "meals_reconcile_vault_recipes"
        self.conn.execute(f"SAVEPOINT {savepoint}")
        try:
            result = self._reconcile_vault_recipes_inner(root)
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        self.conn.commit()
        return result

    def _reconcile_vault_recipes_inner(self, vault_root: Path) -> ReconcileRecipesResult:
        root = vault_root.resolve()
        rows = self.conn.execute(
            """
            SELECT id, normalized_title AS slug, vault_path
            FROM meals_recipes
            WHERE vault_path IS NOT NULL
            """
        ).fetchall()
        orphaned_recipe_ids: list[int] = []
        orphaned = 0
        checked = len(rows)
        for row in rows:
            recipe_id = int(row["id"])
            slug = str(row["slug"])
            relative = str(row["vault_path"])
            if (root / relative).is_file():
                continue
            previous = relative
            self.conn.execute(
                """
                UPDATE meals_recipes
                SET vault_path = NULL, vault_synced_at = NULL
                WHERE id = ?
                """,
                (recipe_id,),
            )
            event_id = emit_event(
                self.conn,
                event_type="meals.recipe_orphaned",
                domain="meals",
                occurred_at=utc_now_isoformat(timespec="seconds"),
                entity_ref=f"recipe-{recipe_id}",
                source=EVENT_SOURCE,
                payload={
                    "recipe_id": recipe_id,
                    "slug": slug,
                    "previous_vault_path": previous,
                    "reason": "vault_file_missing",
                },
            )
            if event_id is None:
                raise RuntimeError("meals.recipe_orphaned event emission failed")
            orphaned += 1
            orphaned_recipe_ids.append(recipe_id)
        return ReconcileRecipesResult(
            checked=checked, orphaned=orphaned, orphaned_recipe_ids=orphaned_recipe_ids
        )

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
        return [
            _recipe_from_row(
                row, self._ingredients(int(row["id"])), self._substitutions(int(row["id"]))
            )
            for row in rows
        ]

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


def _emit_nutrition_day_updated(conn: Connection, *, occurred_at: str) -> None:
    timezone_name = resolve_timezone_name(conn)
    day = local_calendar_date_for_utc_timestamp(occurred_at, timezone_name)
    start_utc, end_utc = local_day_utc_bounds(day, timezone_name)
    rows = conn.execute(
        """
        SELECT protein_grams, calories
        FROM meals_meal_entries
        WHERE datetime(occurred_at) >= datetime(?)
          AND datetime(occurred_at) < datetime(?)
        ORDER BY occurred_at ASC, id ASC
        """,
        (start_utc, end_utc),
    ).fetchall()
    protein_values = [float(row["protein_grams"]) for row in rows if row["protein_grams"] is not None]
    calorie_values = [int(row["calories"]) for row in rows if row["calories"] is not None]
    event_id = emit_event(
        conn,
        event_type="nutrition.day_updated",
        domain="meals",
        occurred_at=occurred_at,
        entity_ref=f"nutrition-{day}",
        source=EVENT_SOURCE,
        payload={
            "date": day,
            "meal_count": len(rows),
            "protein_grams": sum(protein_values) if protein_values else None,
            "calories": sum(calorie_values) if calorie_values else None,
        },
    )
    if event_id is None:
        raise RuntimeError("nutrition.day_updated event emission failed")


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
    vault_cell = row["vault_path"]
    return Recipe(
        id=int(row["id"]),
        vault_path=str(vault_cell) if vault_cell is not None else "",
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
