from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime, timedelta
from sqlite3 import Connection
from zoneinfo import ZoneInfo

from minx_mcp.core.models import NutritionSnapshot
from minx_mcp.meals.models import PantryItem
from minx_mcp.meals.pantry import pantry_item_from_row
from minx_mcp.preferences import get_preference
from minx_mcp.time_utils import format_utc_timestamp, normalize_utc_timestamp


class MealsReadAPI:
    def __init__(self, db: Connection) -> None:
        self._db = db

    def get_nutrition_summary(self, date: str) -> NutritionSnapshot:
        start_utc, end_utc = _local_day_utc_bounds(date, _resolve_timezone_name(self._db))
        candidate_rows = self._db.execute(
            """
            SELECT id, occurred_at, meal_kind, protein_grams, calories
            FROM meals_meal_entries
            ORDER BY occurred_at ASC, id ASC
            """
        ).fetchall()
        normalized_rows = [
            (normalize_utc_timestamp(str(row["occurred_at"])), row)
            for row in candidate_rows
        ]
        rows = [
            row
            for normalized, row in sorted(normalized_rows, key=lambda item: (item[0], int(item[1]["id"])))
            if start_utc <= normalized < end_utc
        ]
        meal_kinds = {str(row["meal_kind"]) for row in rows}
        protein_values = [
            float(row["protein_grams"])
            for row in rows
            if row["protein_grams"] is not None
        ]
        calorie_values = [
            int(row["calories"]) for row in rows if row["calories"] is not None
        ]
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
