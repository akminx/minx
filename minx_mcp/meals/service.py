from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from sqlite3 import Connection, Row
from typing import Self

from minx_mcp.contracts import InvalidInputError, NotFoundError
from minx_mcp.core.events import emit_event
from minx_mcp.db import get_connection
from minx_mcp.meals.models import (
    MealEntry,
    PantryItem,
    Recipe,
    RecipeIngredient,
    RecipeSubstitution,
    ShoppingList,
    ShoppingListItem,
)
from minx_mcp.meals.pantry import normalize_ingredient, pantry_item_from_row
from minx_mcp.meals.recipes import parse_recipe_note
from minx_mcp.meals.shopping import missing_shopping_items
from minx_mcp.vault_writer import VaultWriter

EVENT_SOURCE = "meals.service"
VALID_MEAL_KINDS = {"breakfast", "lunch", "dinner", "snack", "other"}
logger = logging.getLogger(__name__)


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
        meal_id = cursor.lastrowid or 0
        emit_event(
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
        self.conn.commit()
        return self._get_meal_entry(meal_id)

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
        params: tuple[str, str] | tuple[()] = ()
        where = ""
        if date is not None:
            where = "WHERE occurred_at >= ? AND occurred_at < date(?, '+1 day')"
            params = (date, date)
        rows = self.conn.execute(
            f"""
            SELECT id, occurred_at, meal_kind, summary, food_items_json, protein_grams,
                   calories, carbs_grams, fat_grams, notes, source
            FROM meals_meal_entries
            {where}
            ORDER BY occurred_at ASC, id ASC
            """,
            params,
        ).fetchall()
        return [_meal_entry_from_row(row) for row in rows]

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

    def generate_shopping_list(self, recipe_id: int) -> ShoppingList:
        recipe = self.get_recipe(recipe_id)
        drafts = missing_shopping_items(recipe, self.list_pantry_items())
        if not drafts:
            raise InvalidInputError(f"recipe {recipe_id} does not need a shopping list")

        shopping_list_id = 0
        artifact_path: str | None = None
        try:
            cursor = self.conn.execute(
                """
                INSERT INTO meals_shopping_lists (recipe_id, title)
                VALUES (?, ?)
                """,
                (recipe_id, f"Shopping List: {recipe.title}"),
            )
            shopping_list_id = int(cursor.lastrowid or 0)
            for draft in drafts:
                self.conn.execute(
                    """
                    INSERT INTO meals_shopping_list_items (
                        shopping_list_id, recipe_ingredient_id, display_text, normalized_name,
                        quantity, unit, pantry_quantity, missing_quantity, pantry_unit, notes, sort_order
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        shopping_list_id,
                        draft.ingredient.id,
                        draft.ingredient.display_text,
                        draft.ingredient.normalized_name,
                        draft.ingredient.quantity,
                        draft.ingredient.unit,
                        draft.pantry_quantity,
                        draft.missing_quantity,
                        draft.pantry_unit,
                        draft.notes,
                        draft.ingredient.sort_order,
                    ),
                )

            if self._vault_root is not None:
                shopping_list = self.get_shopping_list(shopping_list_id)
                artifact_path = self._write_shopping_list_artifact(shopping_list, recipe.vault_path)
                if artifact_path is not None:
                    self.conn.execute(
                        """
                        UPDATE meals_shopping_lists
                        SET vault_path = ?, updated_at = datetime('now')
                        WHERE id = ?
                        """,
                        (artifact_path, shopping_list_id),
                    )
            self.conn.commit()
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            if artifact_path is not None and self._vault_root is not None:
                artifact_file = (self._vault_root / artifact_path).resolve()
                try:
                    if artifact_file.exists():
                        artifact_file.unlink()
                except OSError:
                    logger.exception("Failed to clean up shopping list artifact after rollback")
            raise
        return self.get_shopping_list(shopping_list_id)

    def get_shopping_list(self, shopping_list_id: int) -> ShoppingList:
        row = self.conn.execute(
            """
            SELECT l.id, l.recipe_id, r.title AS recipe_title, l.title, l.vault_path, l.status, l.created_at
            FROM meals_shopping_lists l
            JOIN meals_recipes r ON r.id = l.recipe_id
            WHERE l.id = ?
            """,
            (shopping_list_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"shopping list {shopping_list_id} not found")
        return _shopping_list_from_row(row, self._shopping_list_items(shopping_list_id))

    def _shopping_list_items(self, shopping_list_id: int) -> list[ShoppingListItem]:
        rows = self.conn.execute(
            """
            SELECT id, shopping_list_id, recipe_ingredient_id, display_text, normalized_name,
                   quantity, unit, pantry_quantity, missing_quantity, pantry_unit, notes, sort_order
            FROM meals_shopping_list_items
            WHERE shopping_list_id = ?
            ORDER BY sort_order ASC, id ASC
            """,
            (shopping_list_id,),
        ).fetchall()
        return [_shopping_list_item_from_row(row) for row in rows]

    def _write_shopping_list_artifact(
        self,
        shopping_list: ShoppingList,
        recipe_vault_path: str,
    ) -> str | None:
        if self._vault_root is None:
            return None

        writer = VaultWriter(self._vault_root, ("Generated",))
        vault_path = f"Generated/Shopping Lists/shopping-list-{shopping_list.id}.md"
        body = "\n".join(f"- [ ] {_shopping_item_text(item)}" for item in shopping_list.items)
        content = (
            f"# {shopping_list.title}\n\n"
            f"Source recipe: {recipe_vault_path}\n\n"
            f"{body}\n"
        )
        writer.write_markdown(vault_path, content)
        return vault_path

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


def _shopping_list_from_row(row: Row, items: list[ShoppingListItem]) -> ShoppingList:
    return ShoppingList(
        id=int(row["id"]),
        recipe_id=int(row["recipe_id"]),
        recipe_title=str(row["recipe_title"]),
        title=str(row["title"]),
        vault_path=row["vault_path"],
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        items=items,
    )


def _shopping_list_item_from_row(row: Row) -> ShoppingListItem:
    return ShoppingListItem(
        id=int(row["id"]),
        shopping_list_id=int(row["shopping_list_id"]),
        recipe_ingredient_id=(
            int(row["recipe_ingredient_id"])
            if row["recipe_ingredient_id"] is not None
            else None
        ),
        display_text=str(row["display_text"]),
        normalized_name=str(row["normalized_name"]),
        quantity=float(row["quantity"]) if row["quantity"] is not None else None,
        unit=row["unit"],
        pantry_quantity=(
            float(row["pantry_quantity"]) if row["pantry_quantity"] is not None else None
        ),
        missing_quantity=(
            float(row["missing_quantity"]) if row["missing_quantity"] is not None else None
        ),
        pantry_unit=row["pantry_unit"],
        notes=row["notes"],
        sort_order=int(row["sort_order"]),
    )


def _shopping_item_text(item: ShoppingListItem) -> str:
    if item.missing_quantity is None:
        return item.display_text
    amount = _format_amount(item.missing_quantity)
    if item.unit is not None and item.unit.strip():
        amount_text = f"{amount}{item.unit.strip()}"
    else:
        amount_text = amount
    return f"{amount_text} {_shopping_item_name(item)}"


def _format_amount(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:g}"


def _shopping_item_name(item: ShoppingListItem) -> str:
    display = item.display_text.strip()
    if not display:
        return item.normalized_name
    if item.quantity is None:
        return display

    quantity_text = _format_amount(item.quantity)
    unit_text = item.unit.strip() if item.unit is not None else ""
    prefixes = [quantity_text]
    if unit_text:
        prefixes = [f"{quantity_text}{unit_text}", f"{quantity_text} {unit_text}", quantity_text]
    for prefix in prefixes:
        token = f"{prefix} "
        if display.lower().startswith(token.lower()):
            trimmed = display[len(token):].strip()
            if trimmed:
                return trimmed
    return display
