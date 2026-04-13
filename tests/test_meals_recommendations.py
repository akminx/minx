from __future__ import annotations

from minx_mcp.meals.recommendations import classify_recipe, recommend_recipes


def test_classify_make_with_substitutions() -> None:
    result = classify_recipe(
        required_names=["pasta", "chickpea"],
        optional_names=["parmesan"],
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
    meals_seeder.recipe_ingredient(recipe_id=salmon, display_text="salmon fillet", normalized_name="salmon")
    meals_seeder.pantry_item(display_name="Pasta", quantity=500, unit="g")

    result = recommend_recipes(db_conn)
    expanded = recommend_recipes(db_conn, include_needs_shopping=True)

    assert [rec.recipe_title for rec in result.recommendations] == ["Simple Pasta"]
    assert [rec.recipe_title for rec in expanded.recommendations] == [
        "Simple Pasta",
        "Grilled Salmon",
    ]
    assert expanded.shopping_lists_generated == []
    count = db_conn.execute("SELECT COUNT(*) FROM meals_shopping_lists").fetchone()[0]
    assert count == 0


def test_recommend_recipes_includes_richer_recipe_metadata(db_conn, meals_seeder) -> None:
    recipe_id = meals_seeder.recipe(
        vault_path="Recipes/Salmon.md",
        title="Salmon Dinner",
        image_ref="Assets/salmon.jpg",
        source_url="https://example.com/salmon",
    )
    db_conn.execute(
        """
        UPDATE meals_recipes
        SET prep_time_minutes = 10,
            cook_time_minutes = 20,
            servings = 2,
            notes = 'Serve with lemon',
            nutrition_summary_json = '{"calories": 500}'
        WHERE id = ?
        """,
        (recipe_id,),
    )
    db_conn.commit()
    meals_seeder.recipe_ingredient(
        recipe_id=recipe_id,
        display_text="1 salmon fillet",
        normalized_name="salmon fillet",
    )

    result = recommend_recipes(db_conn, include_needs_shopping=True)

    recipe = result.recommendations[0].recipe
    assert recipe.prep_time_minutes == 10
    assert recipe.cook_time_minutes == 20
    assert recipe.servings == 2
    assert recipe.notes == "Serve with lemon"
    assert recipe.nutrition_summary == {"calories": 500}
