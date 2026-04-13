from __future__ import annotations

from sqlite3 import Connection, Row

from minx_mcp.meals.models import PantryItem

_PLURAL_SUFFIXES = [("ies", "y"), ("ves", "f"), ("es", ""), ("s", "")]


def normalize_ingredient(name: str) -> str:
    result = name.lower().strip()
    for suffix, replacement in _PLURAL_SUFFIXES:
        if result.endswith(suffix) and len(result) > len(suffix) + 1:
            candidate = result[: -len(suffix)] + replacement
            if len(candidate) >= 3:
                result = candidate
                break
    return result


def match_pantry(conn: Connection, ingredient_names: list[str]) -> dict[str, PantryItem]:
    normalized = [normalize_ingredient(name) for name in ingredient_names]
    if not normalized:
        return {}
    placeholders = ",".join("?" for _ in normalized)
    rows = conn.execute(
        f"""
        SELECT id, display_name, normalized_name, quantity, unit, expiration_date,
               low_stock_threshold, source
        FROM meals_pantry_items
        WHERE normalized_name IN ({placeholders})
        ORDER BY normalized_name ASC, id ASC
        """,
        normalized,
    ).fetchall()
    return {str(row["normalized_name"]): pantry_item_from_row(row) for row in rows}


def get_expiring_items(
    conn: Connection,
    as_of: str,
    days_ahead: int = 3,
) -> list[PantryItem]:
    rows = conn.execute(
        """
        SELECT id, display_name, normalized_name, quantity, unit, expiration_date,
               low_stock_threshold, source
        FROM meals_pantry_items
        WHERE expiration_date IS NOT NULL
          AND expiration_date <= date(?, '+' || ? || ' days')
        ORDER BY expiration_date ASC, normalized_name ASC, id ASC
        """,
        (as_of, days_ahead),
    ).fetchall()
    return [pantry_item_from_row(row) for row in rows]


def get_low_stock_items(conn: Connection) -> list[PantryItem]:
    rows = conn.execute(
        """
        SELECT id, display_name, normalized_name, quantity, unit, expiration_date,
               low_stock_threshold, source
        FROM meals_pantry_items
        WHERE low_stock_threshold IS NOT NULL
          AND quantity IS NOT NULL
          AND quantity < low_stock_threshold
        ORDER BY normalized_name ASC, id ASC
        """
    ).fetchall()
    return [pantry_item_from_row(row) for row in rows]


def pantry_item_from_row(row: Row) -> PantryItem:
    return PantryItem(
        id=int(row["id"]),
        display_name=str(row["display_name"]),
        normalized_name=str(row["normalized_name"]),
        quantity=float(row["quantity"]) if row["quantity"] is not None else None,
        unit=row["unit"],
        expiration_date=row["expiration_date"],
        low_stock_threshold=(
            float(row["low_stock_threshold"])
            if row["low_stock_threshold"] is not None
            else None
        ),
        source=str(row["source"]),
    )

