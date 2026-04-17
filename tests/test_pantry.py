from __future__ import annotations

import pytest

from minx_mcp.meals.pantry import normalize_ingredient


@pytest.mark.parametrize(
    "name,expected",
    [
        # singularization should NOT corrupt these
        ("glass", "glass"),
        ("series", "series"),
        ("dress", "dress"),
        ("chess", "chess"),
        # normal plural stripping should still work
        ("tomatoes", "tomato"),
        ("lemons", "lemon"),
        ("eggs", "egg"),
        ("berries", "berry"),
        # whitespace and casing
        ("  Chicken Breast  ", "chicken breast"),
    ],
)
def test_normalize_ingredient(name: str, expected: str) -> None:
    assert normalize_ingredient(name) == expected
