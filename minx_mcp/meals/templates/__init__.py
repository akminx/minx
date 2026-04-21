"""Markdown template resources for meals.

Templates ship as ``minx_mcp.meals.templates`` package data so wheel-installed
deployments can serve them without relying on the source checkout layout. Mirrors
the pattern used by ``minx_mcp/finance/report_builders.py`` (see
``TEMPLATE_DIR = Path(__file__).resolve().parent`` in that module's sibling
finance templates package).

The recipe starter scaffold is an **input scaffold for humans**: users copy it
into their Obsidian vault and replace the example values with their own recipe.
The scaffold's structure (frontmatter keys and
``## Ingredients`` / ``## Substitutions`` / ``## Notes`` section layout) matches
the contract :func:`minx_mcp.meals.recipes.parse_recipe_note` expects, so a
freshly-copied scaffold indexes cleanly before any edits.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE_DIR: Path = Path(__file__).resolve().parent

_RECIPE_STARTER_FILENAME = "recipe-starter.md"


def recipe_starter_template_path() -> Path:
    """Return the filesystem path to the packaged recipe starter template."""
    return TEMPLATE_DIR / _RECIPE_STARTER_FILENAME


def read_recipe_starter_template() -> str:
    """Return the recipe starter markdown scaffold as a string.

    The returned text is the raw scaffold; callers (harness, MCP tool) can hand
    it to a user verbatim, or substitute placeholder values before writing to a
    vault path. No template interpolation is performed here.
    """
    return recipe_starter_template_path().read_text(encoding="utf-8")


__all__ = [
    "TEMPLATE_DIR",
    "read_recipe_starter_template",
    "recipe_starter_template_path",
]
