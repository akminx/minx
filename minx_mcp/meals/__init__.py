"""Meals domain (nutrition, pantry, recipes, recommendations).

The ``meals_nutrition_cache`` SQLite table from migration 010 is reserved for a future
Phase 3 shopping-list / cached nutrition-lookup path; it is not read or written by
current services so the migration can remain stable while that work is deferred.
"""

from __future__ import annotations

__version__ = "0.1.0"
