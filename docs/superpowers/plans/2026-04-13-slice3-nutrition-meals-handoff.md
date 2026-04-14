# Handoff: Meals Nutrition Layer + Slice 4 Setup

**Date:** 2026-04-13  
**Branch state:** local working tree on `main` (not yet committed in this handoff)  
**Focus:** add nutrition profile/targets into Meals, wire recommendations, and prepare Slice 4 skeleton

## 1) What was implemented

### A) Meals nutrition persistence + calculations

- Added migration `011_meals_nutrition.sql` in both trees:
  - `minx_mcp/schema/migrations/011_meals_nutrition.sql`
  - `schema/migrations/011_meals_nutrition.sql`
- New tables:
  - `meals_nutrition_profiles`
  - `meals_nutrition_targets`
- Added deterministic nutrition calculation module:
  - `minx_mcp/meals/nutrition.py`
- Added service methods:
  - `set_nutrition_profile(...)`
  - `get_nutrition_profile()`
  - `get_nutrition_targets()`
  - `get_nutrition_plan()`

### B) Recommendation integration with nutrition targets

- Recommendation output now carries nutrition context (when profile exists).
- Recipes include per-recipe nutrition fit metadata in recommendations.
- Added optional recommendation filter:
  - `apply_nutrition_filter` (exclude recipes that exceed remaining calorie budget).
- Ranking now considers nutrition fit before tie-breakers.

### C) MCP tools

- Added:
  - `nutrition_profile_set`
  - `nutrition_profile_get`
- Updated:
  - `recommend_recipes(include_needs_shopping, apply_nutrition_filter)`

### D) Model extensions

- Added dataclasses for:
  - nutrition profile/targets/plan
  - recommendation nutrition context
  - recipe nutrition fit
- Extended recipe metadata payload in recommendation outputs to include prep/cook/servings/notes/nutrition summary.

## 2) Tests added/updated

- `tests/test_meals_service.py`
  - profile persistence + target-calculation assertions
  - validation behavior
- `tests/test_meals_recommendations.py`
  - ranking/filtering behavior with nutrition targets
  - nutrition context assertions
- `tests/test_meals_server.py`
  - new tool registration and tool-call behavior
- `tests/test_db.py`
  - new nutrition table existence
  - migration count/wheel inclusion updates for `011`
- `tests/test_migration_checksums.py`
  - last migration order expectation updated to `011`
- `tests/helpers.py`
  - seeder supports `nutrition_summary` for recipes

## 3) Verification run

Executed successfully:

1. `uv run pytest -q tests/test_meals_service.py tests/test_meals_recommendations.py tests/test_meals_server.py tests/test_db.py tests/test_migration_checksums.py`
2. `uv run pytest -q`
3. `uv run mypy`
4. `git diff --check`

Results:
- `504 passed`
- mypy clean
- no whitespace/check errors

## 4) Slice 4 planning artifact created

- `docs/superpowers/specs/2026-04-13-slice4-training-mcp-skeleton.md`

This is a concrete implementation skeleton: package shape, schema/tool skeleton, detector skeleton, core integration, Hermes flow, tests, and DoD.

## 5) Recommended next execution order

1. Commit this nutrition-layer change set.
2. Start Slice 4 implementation from the new skeleton:
   - training schema + models + service
   - training MCP tools
   - core `TrainingSnapshot` integration
   - detector v1 set
3. Hook Hermes harness to call:
   - meals nutrition tools
   - training tools
   - core daily snapshot
4. Run end-to-end scripted scenario:
   - set nutrition profile
   - log meals
   - log workout sessions
   - fetch recommendations/snapshot
   - verify cross-domain signals

## 6) Notes / caveats

- Nutrition fit currently relies on recipe `nutrition_summary_json` fields when present.
- Protein fit uses a capped per-meal threshold heuristic for ranking support.
- No harness-specific code was added; this remains MCP-layer deterministic behavior only.
