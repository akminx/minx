from __future__ import annotations

import logging

from minx_mcp.meals.pantry import (
    get_expiring_items,
    get_low_stock_items,
    match_pantry,
    normalize_ingredient,
)


def test_normalize_ingredient_basic_and_plural() -> None:
    assert normalize_ingredient("Chicken Breast") == "chicken breast"
    assert normalize_ingredient("tomatoes") == "tomato"
    assert normalize_ingredient("eggs") == "egg"


def test_match_pantry_signals(db_conn, meals_seeder) -> None:
    meals_seeder.pantry_item(display_name="Pasta", quantity=500, unit="g")
    meals_seeder.pantry_item(
        display_name="Spinach",
        quantity=200,
        unit="g",
        expiration_date="2026-04-14",
    )
    meals_seeder.pantry_item(
        display_name="Eggs",
        quantity=2,
        unit="count",
        low_stock_threshold=6,
    )

    assert "pasta" in match_pantry(db_conn, ["pasta", "salmon"])
    assert [item.display_name for item in get_expiring_items(db_conn, "2026-04-12")] == ["Spinach"]
    assert [item.display_name for item in get_low_stock_items(db_conn)] == ["Eggs"]


def test_match_pantry_aggregates_rows_with_same_normalized_name_and_unit(db_conn) -> None:
    db_conn.executescript(
        """
        INSERT INTO meals_pantry_items (
            display_name, normalized_name, quantity, unit, expiration_date, source
        ) VALUES ('Tomato', 'tomato', 3, 'count', '2026-06-01', 'pantry');
        INSERT INTO meals_pantry_items (
            display_name, normalized_name, quantity, unit, expiration_date, source
        ) VALUES ('Tomato', 'tomato', 5, 'count', '2026-04-01', 'fridge');
        """
    )
    matches = match_pantry(db_conn, ["tomato"])
    m = matches["tomato"]
    assert m.quantity == 8.0
    assert m.sources == ["fridge", "pantry"]
    assert m.expiration_date == "2026-04-01"


def test_match_pantry_keeps_largest_when_units_conflict(db_conn, caplog) -> None:
    db_conn.executescript(
        """
        INSERT INTO meals_pantry_items (
            display_name, normalized_name, quantity, unit, expiration_date, source
        ) VALUES ('Tomato', 'tomato', 3, 'count', NULL, 'pantry');
        INSERT INTO meals_pantry_items (
            display_name, normalized_name, quantity, unit, expiration_date, source
        ) VALUES ('Tomato', 'tomato', 0.5, 'kg', NULL, 'fridge');
        """
    )
    with caplog.at_level(logging.WARNING, logger="minx_mcp.meals.pantry"):
        matches = match_pantry(db_conn, ["tomato"])
    m = matches["tomato"]
    assert m.unit == "count"
    assert m.quantity == 3.0
    assert m.sources == ["pantry"]
    assert "conflicting units" in caplog.text


def test_match_pantry_single_row_still_has_sources_list(db_conn) -> None:
    db_conn.execute(
        """
        INSERT INTO meals_pantry_items (
            display_name, normalized_name, quantity, unit, expiration_date, source
        ) VALUES ('Tomato', 'tomato', 2, 'count', NULL, 'pantry')
        """
    )
    matches = match_pantry(db_conn, ["tomato"])
    assert matches["tomato"].sources == ["pantry"]
