from __future__ import annotations

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
