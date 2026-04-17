from __future__ import annotations

from minx_mcp.meals.recommendations import classify_recipe, recommend_recipes
from minx_mcp.meals.service import MealsService


def test_classify_make_with_substitutions() -> None:
    result = classify_recipe(
        required_names=["pasta", "chickpea"],
        pantry_names={"pasta", "white bean"},
        substitution_map={"chickpea": ["white bean"]},
    )

    assert result.availability_class == "make_with_substitutions"
    assert result.substitution_count == 1
    assert result.missing_required_count == 0


def test_recommend_recipes_default_filters_needs_shopping(db_conn, meals_seeder) -> None:
    pasta = meals_seeder.recipe(vault_path="Recipes/Pasta.md", title="Simple Pasta")
    meals_seeder.recipe_ingredient(recipe_id=pasta, display_text="pasta", normalized_name="pasta")
    salmon = meals_seeder.recipe(vault_path="Recipes/Salmon.md", title="Grilled Salmon")
    meals_seeder.recipe_ingredient(
        recipe_id=salmon, display_text="salmon fillet", normalized_name="salmon"
    )
    meals_seeder.pantry_item(display_name="Pasta", quantity=500, unit="g")

    result = recommend_recipes(db_conn)
    expanded = recommend_recipes(db_conn, include_needs_shopping=True)

    assert [rec.recipe_title for rec in result.recommendations] == ["Simple Pasta"]
    assert [rec.recipe_title for rec in expanded.recommendations] == [
        "Simple Pasta",
        "Grilled Salmon",
    ]


def test_recommend_recipes_uses_nutrition_targets_for_ranking_and_filtering(db_path) -> None:
    with MealsService(db_path) as svc:
        light = svc.conn.execute(
            """
            INSERT INTO meals_recipes (
                vault_path, title, normalized_title, tags_json, nutrition_summary_json, content_hash
            ) VALUES (?, ?, ?, '[]', ?, ?)
            """,
            (
                "Recipes/Light Bowl.md",
                "Light Bowl",
                "light bowl",
                '{"calories": 520, "protein_grams": 48}',
                "h1",
            ),
        ).lastrowid
        svc.conn.execute(
            """
            INSERT INTO meals_recipe_ingredients (
                recipe_id, display_text, normalized_name, is_required, sort_order
            ) VALUES (?, 'chicken', 'chicken', 1, 0)
            """,
            (light,),
        )
        heavy = svc.conn.execute(
            """
            INSERT INTO meals_recipes (
                vault_path, title, normalized_title, tags_json, nutrition_summary_json, content_hash
            ) VALUES (?, ?, ?, '[]', ?, ?)
            """,
            (
                "Recipes/Heavy Pasta.md",
                "Heavy Pasta",
                "heavy pasta",
                '{"calories": 920, "protein_grams": 26}',
                "h2",
            ),
        ).lastrowid
        svc.conn.execute(
            """
            INSERT INTO meals_recipe_ingredients (
                recipe_id, display_text, normalized_name, is_required, sort_order
            ) VALUES (?, 'pasta', 'pasta', 1, 0)
            """,
            (heavy,),
        )
        svc.add_pantry_item(display_name="Chicken", quantity=500, unit="g")
        svc.add_pantry_item(display_name="Pasta", quantity=500, unit="g")
        svc.log_meal(
            occurred_at="2026-04-12T12:00:00Z",
            meal_kind="lunch",
            calories=1500,
            protein_grams=80.0,
        )
        svc.set_nutrition_profile(
            sex="male",
            age_years=30,
            height_cm=180.0,
            weight_kg=80.0,
            activity_level="moderately_active",
            goal="maintenance",
            calorie_deficit_kcal=400,
        )
        svc.conn.commit()
        ranked = recommend_recipes(svc.conn, as_of="2026-04-12", include_needs_shopping=True)
        filtered = recommend_recipes(
            svc.conn,
            as_of="2026-04-12",
            include_needs_shopping=True,
            apply_nutrition_filter=True,
        )

    assert [rec.recipe_title for rec in ranked.recommendations][:2] == ["Light Bowl", "Heavy Pasta"]
    assert [rec.recipe_title for rec in filtered.recommendations] == ["Light Bowl"]
    assert ranked.nutrition_context is not None
    assert ranked.nutrition_context.remaining_calorie_budget_kcal == 859
