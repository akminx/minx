from __future__ import annotations

from minx_mcp.meals.recipes import parse_recipe_note


def test_parse_recipe_frontmatter_minute_strings_and_obsidian_image(tmp_path) -> None:
    note = tmp_path / "Breakfast Mac n Cheese.md"
    note.write_text(
        "---\n"
        "date: 2026-03-25\n"
        "tags: [high-protein, air-fryer, meal-prep]\n"
        "prep_time: 10 min\n"
        "cook_time: 25 min\n"
        "servings: 2\n"
        "status: #active\n"
        "---\n\n"
        "# Breakfast Mac n Cheese\n\n"
        "![[assets/Breakfast Mac n Cheese.png]]\n\n"
        "## Ingredients\n"
        "- 112g protein pasta\n"
        "- **Toppings:**\n"
        "  - 20g 2% cheddar\n"
        "- Salt and pepper to taste (optional)\n"
        "## Substitutions\n"
        "- pasta: chickpea pasta, lentil pasta\n"
    )

    result = parse_recipe_note(note)

    assert result.title == "Breakfast Mac n Cheese"
    assert result.tags == ["high-protein", "air-fryer", "meal-prep"]
    assert result.prep_time_minutes == 10
    assert result.cook_time_minutes == 25
    assert result.image_ref == "assets/Breakfast Mac n Cheese.png"
    assert [ingredient.display_text for ingredient in result.ingredients] == [
        "112g protein pasta",
        "20g 2% cheddar",
        "Salt and pepper to taste (optional)",
    ]
    assert result.ingredients[-1].is_required is False
    assert {sub.substitute_name for sub in result.substitutions} == {
        "chickpea pasta",
        "lentil pasta",
    }
