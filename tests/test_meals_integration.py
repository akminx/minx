from __future__ import annotations

import pytest

from minx_mcp.core.models import SnapshotContext
from minx_mcp.core.snapshot import build_daily_snapshot
from minx_mcp.db import get_connection
from minx_mcp.meals.recommendations import recommend_recipes
from minx_mcp.meals.service import MealsService


@pytest.mark.asyncio
async def test_full_meals_pipeline(tmp_path) -> None:
    db_path = tmp_path / "minx.db"
    vault = tmp_path / "vault"
    recipes_dir = vault / "Recipes"
    recipes_dir.mkdir(parents=True)
    (recipes_dir / "Quick Pasta.md").write_text(
        "---\ntitle: Quick Pasta\ntags: [dinner]\n---\n"
        "## Ingredients\n- 400g pasta\n- 2 cups spinach\n"
    )
    (recipes_dir / "Grilled Salmon.md").write_text(
        "---\ntitle: Grilled Salmon\ntags: [dinner]\n---\n"
        "## Ingredients\n- 1 salmon fillet\n"
    )

    with MealsService(db_path, vault_root=vault) as svc:
        svc.log_meal(
            occurred_at="2026-04-12T19:00:00Z",
            meal_kind="dinner",
            protein_grams=25.0,
            calories=600,
        )
        svc.add_pantry_item(display_name="Pasta", quantity=500, unit="g")
        svc.add_pantry_item(display_name="Spinach", quantity=200, unit="g", expiration_date="2026-04-14")
        svc.scan_vault_recipes()

    conn = get_connection(db_path)
    try:
        result = recommend_recipes(conn, as_of="2026-04-12", include_needs_shopping=True)
    finally:
        conn.close()

    snapshot = await build_daily_snapshot("2026-04-12", SnapshotContext(db_path=db_path))

    assert [rec.recipe_title for rec in result.recommendations] == [
        "Quick Pasta",
        "Grilled Salmon",
    ]
    assert result.shopping_lists_generated == []
    assert snapshot.nutrition is not None
    assert snapshot.nutrition.meal_count == 1
    assert "nutrition.low_protein" in {signal.insight_type for signal in snapshot.signals}
