from __future__ import annotations

from sqlite3 import Connection

from minx_mcp.core.models import NutritionSnapshot
from minx_mcp.meals.models import PantryItem
from minx_mcp.meals.pantry import pantry_item_from_row
from minx_mcp.time_utils import local_day_utc_bounds, resolve_timezone_name


class MealsReadAPI:
    def __init__(self, db: Connection) -> None:
        self._db = db

    def get_nutrition_summary(self, date: str) -> NutritionSnapshot:
        start_utc, end_utc = local_day_utc_bounds(date, resolve_timezone_name(self._db))
        rows = self._db.execute(
            """
            SELECT id, occurred_at, meal_kind, protein_grams, calories
            FROM meals_meal_entries
            WHERE datetime(occurred_at) >= datetime(?)
              AND datetime(occurred_at) < datetime(?)
            ORDER BY occurred_at ASC, id ASC
            """,
            (start_utc, end_utc),
        ).fetchall()
        meal_kinds = {str(row["meal_kind"]) for row in rows}
        protein_values = [
            float(row["protein_grams"]) for row in rows if row["protein_grams"] is not None
        ]
        calorie_values = [int(row["calories"]) for row in rows if row["calories"] is not None]
        return NutritionSnapshot(
            date=date,
            meal_count=len(rows),
            protein_grams=sum(protein_values) if protein_values else None,
            calories=sum(calorie_values) if calorie_values else None,
            last_meal_at=str(rows[-1]["occurred_at"]) if rows else None,
            skipped_meal_signals=[
                f"no {kind} logged"
                for kind in ("breakfast", "lunch", "dinner")
                if kind not in meal_kinds
            ]
            if rows
            else [],
        )

    def get_pantry_items(self) -> list[PantryItem]:
        rows = self._db.execute(
            """
            SELECT id, display_name, normalized_name, quantity, unit, expiration_date,
                   low_stock_threshold, source
            FROM meals_pantry_items
            ORDER BY normalized_name ASC, id ASC
            """
        ).fetchall()
        return [pantry_item_from_row(row) for row in rows]
