from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from sqlite3 import Connection, Row

from minx_mcp.meals.models import PantryItem

_logger = logging.getLogger(__name__)

_PLURAL_SUFFIXES = [("ies", "y"), ("ves", "f"), ("es", ""), ("s", "")]

# Words that must not be singularized (suffix stripping would corrupt them).
_SINGULARIZATION_EXCEPTIONS: frozenset[str] = frozenset(
    [
        "asparagus",
        "bass",
        "chess",
        "class",
        "dress",
        "gas",
        "glass",
        "grass",
        "hummus",
        "lentils",
        "mass",
        "molasses",
        "moss",
        "pass",
        "series",
        "species",
        "toss",
    ]
)


def normalize_ingredient(name: str) -> str:
    result = name.lower().strip()
    if result in _SINGULARIZATION_EXCEPTIONS:
        return result
    for suffix, replacement in _PLURAL_SUFFIXES:
        if result.endswith(suffix) and len(result) > len(suffix) + 1:
            candidate = result[: -len(suffix)] + replacement
            if len(candidate) >= 3:
                result = candidate
                break
    return result


@dataclass(frozen=True)
class MatchedPantryItem(PantryItem):
    """Pantry row(s) keyed by ``normalized_name``, with ``sources`` for duplicate rows."""

    sources: list[str] = field(default_factory=list)


def match_pantry(conn: Connection, ingredient_names: list[str]) -> dict[str, MatchedPantryItem]:
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
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["normalized_name"])].append(row)
    return {name: _aggregate_matched_rows(name, group) for name, group in grouped.items()}


def _aggregate_matched_rows(normalized_name: str, group: list[Row]) -> MatchedPantryItem:
    if len(group) == 1:
        base = pantry_item_from_row(group[0])
        return MatchedPantryItem(
            id=base.id,
            display_name=base.display_name,
            normalized_name=base.normalized_name,
            quantity=base.quantity,
            unit=base.unit,
            expiration_date=base.expiration_date,
            low_stock_threshold=base.low_stock_threshold,
            source=base.source,
            sources=[str(group[0]["source"])],
        )

    units = {row["unit"] for row in group}
    if len(units) != 1:
        chosen = max(group, key=_quantity_sort_key)
        base = pantry_item_from_row(chosen)
        _logger.warning(
            "pantry duplicate rows for %r: conflicting units %s; keeping source %r",
            normalized_name,
            sorted(str(u) if u is not None else "None" for u in units),
            str(chosen["source"]),
            extra={
                "normalized_name": normalized_name,
                "conflicting_units": sorted(units, key=lambda u: (u is None, str(u))),
                "chosen_source": str(chosen["source"]),
            },
        )
        return MatchedPantryItem(
            id=base.id,
            display_name=base.display_name,
            normalized_name=base.normalized_name,
            quantity=base.quantity,
            unit=base.unit,
            expiration_date=base.expiration_date,
            low_stock_threshold=base.low_stock_threshold,
            source=base.source,
            sources=[str(chosen["source"])],
        )

    dates = [str(r["expiration_date"]) for r in group if r["expiration_date"] is not None]
    min_exp = min(dates) if dates else None
    primary_candidates = (
        [r for r in group if r["expiration_date"] == min_exp] if min_exp is not None else group
    )
    primary = min(primary_candidates, key=lambda r: int(r["id"]))
    base = pantry_item_from_row(primary)

    qty_parts = [r["quantity"] for r in group if r["quantity"] is not None]
    total_qty: float | None = (
        float(sum(Decimal(str(float(q))) for q in qty_parts)) if qty_parts else None
    )

    sources_sorted = sorted({str(r["source"]) for r in group})

    return MatchedPantryItem(
        id=base.id,
        display_name=base.display_name,
        normalized_name=base.normalized_name,
        quantity=total_qty,
        unit=base.unit,
        expiration_date=min_exp,
        low_stock_threshold=base.low_stock_threshold,
        source=base.source,
        sources=sources_sorted,
    )


def _quantity_sort_key(row: Row) -> float:
    q = row["quantity"]
    return float(q) if q is not None else float("-inf")


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
            float(row["low_stock_threshold"]) if row["low_stock_threshold"] is not None else None
        ),
        source=str(row["source"]),
    )
