from __future__ import annotations

from pathlib import Path

from minx_mcp.meals.recipes import parse_recipe_note
from minx_mcp.meals.templates import (
    TEMPLATE_DIR,
    read_recipe_starter_template,
    recipe_starter_template_path,
)


def test_recipe_starter_template_path_resolves_to_packaged_file() -> None:
    path = recipe_starter_template_path()

    assert path.exists(), f"packaged recipe starter scaffold missing: {path}"
    assert path.parent == TEMPLATE_DIR
    assert path.name == "recipe-starter.md"


def test_read_recipe_starter_template_returns_expected_sections() -> None:
    content = read_recipe_starter_template()

    assert content.startswith("---\n"), "scaffold must begin with YAML frontmatter delimiter"
    assert "## Ingredients\n" in content
    assert "## Substitutions\n" in content
    assert "## Notes\n" in content
    assert "# Your Recipe Title" in content


def test_recipe_starter_scaffold_parses_through_meals_indexer(tmp_path) -> None:
    """The scaffold is shipped for users to copy into their vault. A freshly-copied
    scaffold must index cleanly before any edits, so parse_recipe_note() drives its
    structure."""
    note = tmp_path / "Recipe Starter.md"
    note.write_text(read_recipe_starter_template(), encoding="utf-8")

    recipe = parse_recipe_note(note)

    assert recipe.title == "Your Recipe Title"
    assert recipe.tags == ["weeknight", "high-protein"]
    assert recipe.prep_time_minutes == 10
    assert recipe.cook_time_minutes == 15
    assert recipe.servings == 2
    assert recipe.source_url is None, "empty source: must parse to None, not empty string"
    assert recipe.image_ref is None

    display_texts = [ingredient.display_text for ingredient in recipe.ingredients]
    assert display_texts == [
        "2 eggs",
        "2 slices sourdough bread",
        "1 tbsp butter",
        "Salt and pepper to taste (optional)",
    ]
    assert recipe.ingredients[-1].is_required is False
    assert all(i.is_required for i in recipe.ingredients[:-1])

    substitute_names = {sub.substitute_name for sub in recipe.substitutions}
    assert {"olive oil", "ghee", "whole wheat bread"}.issubset(substitute_names)

    assert recipe.notes is not None
    assert "Replace the fields above" in recipe.notes


def test_recipe_starter_template_is_stable_bytes() -> None:
    """Callers (harness, MCP tool) treat the scaffold as an authored artifact, not a
    string.Template. Guarding here ensures a reviewer who later introduces
    ``${placeholder}`` syntax notices this test surface and decides consciously."""
    content = read_recipe_starter_template()

    assert "${" not in content, (
        "recipe starter is an input scaffold (plain markdown), not a string.Template. "
        "If you're adding placeholder substitution, introduce a separate filled-template "
        "pathway and keep the human-copy scaffold plain."
    )


def test_recipe_starter_template_dir_is_package_directory() -> None:
    """Guards against accidentally pointing TEMPLATE_DIR outside the package tree
    (e.g., at a repo-root ``templates/`` directory), which would break wheel installs
    — the exact regression fixed for finance templates in the prior session."""
    package_root = Path(__file__).resolve().parent.parent / "minx_mcp" / "meals" / "templates"

    assert package_root == TEMPLATE_DIR
