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


def test_parse_recipe_note_nutrition_json_frontmatter(tmp_path) -> None:
    note = tmp_path / "Nutrition.md"
    note.write_text(
        '---\ntitle: Power Bowl\nNutrition: {"calories": 600, "protein_grams": 50}\n---\n'
        "## Ingredients\n- chicken\n"
    )
    result = parse_recipe_note(note)
    assert result.nutrition_summary == {"calories": 600, "protein_grams": 50}


def test_parse_recipe_note_nutrition_indented_block(tmp_path) -> None:
    note = tmp_path / "Block.md"
    note.write_text(
        "---\ntitle: Block\nnutrition:\n  calories: 420\n  protein_grams: 33\n---\n"
        "## Ingredients\n- tofu\n"
    )
    result = parse_recipe_note(note)
    assert result.nutrition_summary == {"calories": 420, "protein_grams": 33}


def test_parse_recipe_note_handles_crlf_line_endings(tmp_path) -> None:
    """Windows-style CRLF files must still yield valid frontmatter parsing."""
    note = tmp_path / "CRLF.md"
    note.write_bytes(
        b"---\r\n"
        b"title: CRLF Recipe\r\n"
        b"tags: [quick]\r\n"
        b"servings: 2\r\n"
        b"---\r\n"
        b"\r\n"
        b"## Ingredients\r\n"
        b"- 100g oats\r\n"
    )
    result = parse_recipe_note(note)
    assert result.title == "CRLF Recipe"
    assert result.tags == ["quick"]
    assert result.servings == 2
    assert [ingredient.display_text for ingredient in result.ingredients] == [
        "100g oats"
    ]


def test_parse_recipe_note_handles_utf8_bom(tmp_path) -> None:
    """Files emitted with a UTF-8 BOM (e.g. Windows Notepad) must parse and
    produce a stable content_hash keyed off raw on-disk bytes."""
    bom = b"\xef\xbb\xbf"
    body = (
        b"---\n"
        b"title: BOM Recipe\n"
        b"servings: 1\n"
        b"---\n"
        b"## Ingredients\n"
        b"- 50g almonds\n"
    )
    note = tmp_path / "BOM.md"
    note.write_bytes(bom + body)
    result = parse_recipe_note(note)
    assert result.title == "BOM Recipe"
    assert result.servings == 1
    assert [ingredient.display_text for ingredient in result.ingredients] == [
        "50g almonds"
    ]

    # Hash should cover raw bytes so a BOM change is observable as a
    # re-index signal (see meals/service._reconcile_vault_recipes_inner).
    plain_note = tmp_path / "NoBom.md"
    plain_note.write_bytes(body)
    plain_result = parse_recipe_note(plain_note)
    assert plain_result.content_hash != result.content_hash
