from __future__ import annotations

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.meals.models import PantryItem, Recipe, RecipeIngredient, RecipeSubstitution
from minx_mcp.meals.service import MealsService
from minx_mcp.meals.shopping import missing_shopping_items


def _ingredient(
    *,
    ingredient_id: int,
    display_text: str,
    normalized_name: str,
    quantity: float | None = None,
    unit: str | None = None,
    is_required: bool = True,
) -> RecipeIngredient:
    return RecipeIngredient(
        id=ingredient_id,
        recipe_id=1,
        display_text=display_text,
        normalized_name=normalized_name,
        quantity=quantity,
        unit=unit,
        is_required=is_required,
        ingredient_group=None,
        sort_order=ingredient_id,
        notes=None,
    )


def _recipe(
    ingredients: list[RecipeIngredient],
    substitutions: list[RecipeSubstitution] | None = None,
) -> Recipe:
    return Recipe(
        id=1,
        vault_path="Recipes/Test.md",
        title="Test Recipe",
        normalized_title="test recipe",
        source_url=None,
        image_ref=None,
        prep_time_minutes=None,
        cook_time_minutes=None,
        servings=None,
        tags=[],
        notes=None,
        nutrition_summary=None,
        content_hash="abc123",
        ingredients=ingredients,
        substitutions=substitutions or [],
    )


def _pantry(name: str, quantity: float | None = None, unit: str | None = None) -> PantryItem:
    return PantryItem(
        id=1,
        display_name=name.title(),
        normalized_name=name,
        quantity=quantity,
        unit=unit,
        expiration_date=None,
        low_stock_threshold=None,
        source="test",
    )


def test_missing_shopping_items_include_required_missing_only() -> None:
    required = _ingredient(
        ingredient_id=1,
        display_text="200g salmon",
        normalized_name="salmon",
        quantity=200,
        unit="g",
    )
    optional = _ingredient(
        ingredient_id=2,
        display_text="lemon wedge",
        normalized_name="lemon",
        is_required=False,
    )

    items = missing_shopping_items(_recipe([required, optional]), pantry_items=[])

    assert [item.ingredient.normalized_name for item in items] == ["salmon"]


def test_missing_shopping_items_diff_against_pantry_quantity() -> None:
    pasta = _ingredient(
        ingredient_id=1,
        display_text="400g pasta",
        normalized_name="pasta",
        quantity=400,
        unit="g",
    )

    items = missing_shopping_items(
        _recipe([pasta]),
        pantry_items=[_pantry("pasta", quantity=150, unit="g")],
    )

    assert len(items) == 1
    assert items[0].missing_quantity == 250
    assert items[0].pantry_quantity == 150


def test_missing_shopping_items_aggregates_duplicate_pantry_quantities() -> None:
    salmon = _ingredient(
        ingredient_id=1,
        display_text="300g salmon",
        normalized_name="salmon",
        quantity=300,
        unit="g",
    )

    items = missing_shopping_items(
        _recipe([salmon]),
        pantry_items=[
            _pantry("salmon", quantity=100, unit="g"),
            _pantry("salmon", quantity=250, unit="g"),
        ],
    )

    assert items == []


def test_missing_shopping_items_exclude_covered_substitution() -> None:
    chickpeas = _ingredient(
        ingredient_id=1,
        display_text="1 cup chickpeas",
        normalized_name="chickpea",
        quantity=1,
        unit="cup",
    )
    substitution = RecipeSubstitution(
        id=1,
        recipe_ingredient_id=1,
        substitute_normalized_name="white bean",
        display_text="white beans",
        quantity=None,
        unit=None,
        priority=0,
        notes=None,
    )

    items = missing_shopping_items(
        _recipe([chickpeas], [substitution]),
        pantry_items=[_pantry("white bean")],
    )

    assert items == []


def test_missing_shopping_items_try_next_substitution_when_first_not_covered() -> None:
    chickpeas = _ingredient(
        ingredient_id=1,
        display_text="1 cup chickpeas",
        normalized_name="chickpea",
        quantity=1,
        unit="cup",
    )
    substitutions = [
        RecipeSubstitution(
            id=1,
            recipe_ingredient_id=1,
            substitute_normalized_name="lentil",
            display_text="lentils",
            quantity=None,
            unit=None,
            priority=0,
            notes=None,
        ),
        RecipeSubstitution(
            id=2,
            recipe_ingredient_id=1,
            substitute_normalized_name="white bean",
            display_text="white beans",
            quantity=None,
            unit=None,
            priority=1,
            notes=None,
        ),
    ]

    items = missing_shopping_items(
        _recipe([chickpeas], substitutions),
        pantry_items=[_pantry("white bean")],
    )

    assert items == []


def test_generate_shopping_list_persists_missing_required_items(db_path, meals_seeder) -> None:
    recipe_id = meals_seeder.recipe(vault_path="Recipes/Salmon.md", title="Salmon Dinner")
    meals_seeder.recipe_ingredient(
        recipe_id=recipe_id,
        display_text="200g salmon",
        normalized_name="salmon",
        quantity=200,
        unit="g",
    )
    meals_seeder.pantry_item(
        display_name="Salmon",
        normalized_name="salmon",
        quantity=50,
        unit="g",
    )

    with MealsService(db_path) as service:
        shopping_list = service.generate_shopping_list(recipe_id)

    assert shopping_list.recipe_id == recipe_id
    assert shopping_list.recipe_title == "Salmon Dinner"
    assert shopping_list.vault_path is None
    assert [
        (item.normalized_name, item.missing_quantity, item.unit)
        for item in shopping_list.items
    ] == [("salmon", 150.0, "g")]


def test_generate_shopping_list_rejects_recipe_without_missing_required_items(
    db_path, meals_seeder
) -> None:
    recipe_id = meals_seeder.recipe(vault_path="Recipes/Pasta.md", title="Pantry Pasta")
    meals_seeder.recipe_ingredient(
        recipe_id=recipe_id,
        display_text="400g pasta",
        normalized_name="pasta",
        quantity=400,
        unit="g",
    )
    meals_seeder.pantry_item(
        display_name="Pasta",
        normalized_name="pasta",
        quantity=500,
        unit="g",
    )

    with pytest.raises(InvalidInputError, match="does not need a shopping list"):
        with MealsService(db_path) as service:
            service.generate_shopping_list(recipe_id)


def test_generate_shopping_list_writes_vault_artifact_when_vault_configured(
    db_path, tmp_path, meals_seeder
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    recipe_id = meals_seeder.recipe(vault_path="Recipes/Salmon.md", title="Salmon Dinner")
    meals_seeder.recipe_ingredient(
        recipe_id=recipe_id,
        display_text="200g salmon",
        normalized_name="salmon",
        quantity=200,
        unit="g",
    )

    with MealsService(db_path, vault_root=vault) as service:
        shopping_list = service.generate_shopping_list(recipe_id)

    assert shopping_list.vault_path is not None
    artifact = vault / shopping_list.vault_path
    assert artifact.exists()
    text = artifact.read_text()
    assert "Salmon Dinner" in text
    assert "200g salmon" in text


def test_generated_shopping_list_survives_recipe_reindex(db_path, tmp_path, meals_seeder) -> None:
    vault = tmp_path / "vault"
    recipe_note = vault / "Recipes" / "Grilled Salmon.md"
    recipe_note.parent.mkdir(parents=True)
    recipe_note.write_text(
        "---\ntitle: Grilled Salmon\n---\n## Ingredients\n- 1 salmon fillet\n"
    )
    recipe_id = meals_seeder.recipe(vault_path="Recipes/Grilled Salmon.md", title="Grilled Salmon")
    meals_seeder.recipe_ingredient(
        recipe_id=recipe_id,
        display_text="1 salmon fillet",
        normalized_name="salmon fillet",
    )

    with MealsService(db_path, vault_root=vault) as service:
        shopping_list = service.generate_shopping_list(recipe_id)
        recipe_note.write_text(
            "---\ntitle: Grilled Salmon\n---\n## Ingredients\n- 1 salmon fillet\n- pinch salt\n"
        )
        service.index_recipe("Recipes/Grilled Salmon.md")
        reloaded = service.get_shopping_list(shopping_list.id)

    assert [item.normalized_name for item in reloaded.items] == ["salmon fillet"]
    assert all(item.recipe_ingredient_id is None for item in reloaded.items)
