# Code Quality Cleanup Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate bugs, dead code, AI slop patterns, and sloppy practices identified in the full codebase audit. Make every module intentional, correct, and maintainable.

**Guiding principle:** If it looks generated, either justify its existence or delete it.

---

## Phase 1: Actual Bugs (Correctness)

These are broken behavior or data-corrupting logic. Fix first.

### 1.1 Fix `MealsService.__new__` hack in recommendations

- In `minx_mcp/meals/recommendations.py`, remove the `MealsService.__new__` bypass (lines 99–104)
- Extract a thin helper or pass `conn` directly to `list_recipes()` / `list_pantry_items()` queries
- Option A: Add a classmethod `MealsService.from_connection(conn)` that takes an existing connection
- Option B: Extract the recipe/pantry queries into standalone functions that accept `conn`
- Verify `test_meals_recommendations.py` still passes

### 1.2 Fix display name casing destruction in training

- In `minx_mcp/training/service.py` `upsert_exercise`, the `display_name` column is set to the lowered `canonical` value instead of the original user-provided `display_name`
- Fix INSERT to use the original `display_name` for the `display_name` column and `canonical` for the `canonical_name` column
- Fix UPDATE similarly
- Add a test: `upsert_exercise("Bench Press")` should store `display_name="Bench Press"`, not `"bench press"`

### 1.3 Fix inconsistent coercion error types in training

- In `minx_mcp/training/service.py`, `_coerce_optional_int` and `_coerce_optional_float` (lines 671–680) raise raw `ValueError` on bad strings
- Wrap with try/except `ValueError` → `InvalidInputError`, matching `_coerce_positive_int` / `_coerce_non_negative_int` behavior
- Same for `_coerce_nullable_string` — `str(value)` silently converts non-strings; should raise `InvalidInputError` for non-string, non-None values
- Add tests for bad input on each coercer

### 1.4 Fix `IncomeSummary` reusing `MerchantSpending` with wrong field names

- In `minx_mcp/finance/read_api.py`, `IncomeSummary.by_source` is typed `list[MerchantSpending]` but holds income data in `total_spent_cents`
- Create an `IncomeSource` dataclass (or rename the field) so the data model does not lie
- Update all callers / tests

### 1.5 Fix Discover PDF parser century bug

- In `minx_mcp/finance/parsers/discover.py` line 24, `f"20{year}"` assumes all years are 20xx
- Fix: if `len(year) == 2`, prepend `"20"`, else use as-is. Or parse with `datetime.strptime` and let it validate
- Add a test with a 4-digit year input

### 1.6 Fix `goals.list_goals(status=None)` surprising behavior

- In `minx_mcp/core/goals.py`, `list_goals(status=None)` silently defaults to `status='active'` instead of returning all goals
- Change: `None` should mean "no filter" (return all goals). Add explicit `status="active"` default at call sites that want only active goals
- Update tests that rely on the current default behavior

### 1.7 Fix `jobs.py` confusing fall-through after stuck job recovery

- In `minx_mcp/jobs.py` (lines 31–64), after recovering a stuck job, the code falls through to insert a new job
- Add a comment explaining this is intentional (recover the old job, then create a fresh one for the retry), OR restructure to make the control flow explicit with a clear separation

### 1.8 Add subprocess timeout to `document_text.py`

- In `minx_mcp/document_text.py`, `subprocess.run` has no `timeout` parameter
- Add `timeout=30` (or configurable) to prevent the MCP tool from hanging indefinitely

---

## Phase 2: Copy-Paste Elimination (DRY)

### 2.1 Extract timezone utilities to `time_utils.py`

- Move `_resolve_timezone_name` and `_local_day_utc_bounds` from `meals/service.py` into `minx_mcp/time_utils.py`
- Replace the copies in: `meals/read_api.py`, `training/service.py`, `training/read_api.py`, `core/read_models.py`
- Run tests to verify all 5 call sites work identically

### 2.2 Extract `_next_day` to shared date utility

- Move `_next_day` from `finance/analytics.py` into `minx_mcp/time_utils.py`
- Replace the copies in: `finance/read_api.py`, `finance/report_builders.py`

### 2.3 Consolidate test helpers

- Move `_call_tool_sync` (or `_call`) into `tests/helpers.py`
- Replace the 6 copies in: `test_core_server.py`, `test_finance_server.py`, `test_goal_parse.py`, `test_meals_server.py`, `test_training_server.py`, `test_contracts.py`
- Move `_TestConfig` into `tests/conftest.py` as a single implementation
- Replace the 3 diverging copies in: `test_end_to_end.py`, `test_core_server.py`, `test_goal_parse.py`

### 2.4 Deduplicate finance import validation

- In `minx_mcp/finance/import_workflow.py`, `run_finance_import` and `preview_finance_import` share ~30 lines of identical path/account/source_kind validation
- Extract a `_validate_import_inputs(service, source_ref, account_name, source_kind)` helper
- Both functions call it, then diverge on actual import vs preview

### 2.5 Deduplicate finance filtered spending/count dispatch

- In `core/goal_progress.py`, `core/goal_detectors.py`, `core/trajectory.py`, the same `if metric_type.startswith("sum_"): get_spending_total else get_transaction_count` branch is repeated
- Extract a `get_metric_value(finance_api, goal, start_date, end_date)` helper (lives in `core/goal_progress.py` or a new `core/goal_metrics.py`)
- All three modules call this helper

### 2.6 Deduplicate `wrap_tool_call` / `wrap_async_tool_call`

- In `minx_mcp/contracts.py`, the sync and async wrappers have identical try/except/log structure
- Extract the shared logic into a helper, or use a decorator pattern that works for both
- Alternatively: keep both but extract the error-handling into `_handle_tool_error(exc, tool_name, start_time)` called by both

### 2.7 Deduplicate entry point boilerplate

- `meals/__main__.py` and `training/__main__.py` are nearly identical
- Extract a shared `run_domain_server(name, create_server_fn, service_cls)` helper into `minx_mcp/transport.py` or a new `minx_mcp/entrypoint.py`
- Both `__main__` files become ~5 lines each

---

## Phase 3: Style and Slop Cleanup

### 3.1 Fix import ordering across all files

- Add `ruff` to dev dependencies with isort rules (`"I"` in select)
- Run `ruff check --fix minx_mcp tests` to auto-fix import ordering
- Specifically fix the `logger = ...` between import blocks in: `core/goal_parse.py`, `finance/import_workflow.py`, `core/events.py`
- Move all `logger`/`_log` assignments after the final import block

### 3.2 Standardize logger naming

- Choose one convention: `logger` everywhere (7 files use it, 1 uses `_log`)
- Rename `_log` to `logger` in `finance/import_workflow.py`

### 3.3 Remove dead code and useless wrappers

- Remove `_validate_goal_create_input_shape` and `_validate_goal_update_input_shape` in `goal_parse.py` (lines 929–934) — they just forward to `_parse_`* with no added logic
- Remove `_utc_now_sql` in `training/service.py` (line 703) — one-line alias for `utc_now_isoformat()`
- Remove dead `optional_names` parameter in `meals/recommendations.py` `classify_recipe` — it's accepted then `del`'d
- Remove dead `shopping_lists_generated` field in `meals/models.py` `RecommendationResult` — always empty list
- Remove dead `_StubFinanceRead` and `_goal_record` from `tests/test_goal_parse.py`
- Remove unused `"finalize"` stage from `finance/rules.py` `RuleStage` and `_STAGE_ORDER` (no code path uses it)
- Remove `_render` from `finance/reports.py` `__all__` — it's a private function

### 3.4 Remove useless comments

- Remove `# Finance parser package.` from `finance/parsers/__init__.py`
- Remove `# Validate existence before mutating global active state.` from `training/service.py` line 323
- Audit for any other narrating comments (there are very few; this codebase is mostly clean on this front)

### 3.5 Fix `money.py` precision issue

- `cents_to_dollars` returns `float`, reintroducing binary floating-point imprecision
- If used only for display, rename to `cents_to_display_dollars` and keep `float`
- If used for computation, return `Decimal` instead
- Audit callers to determine which case applies

### 3.6 Fix naive singularization in `meals/pantry.py`

- `normalize_ingredient` strips plurals with suffix rules that can corrupt names ("glass" → "glas")
- Option A: Remove singularization entirely — match on exact normalized lowercase
- Option B: Add a known-exceptions dict for common false positives
- Add tests for edge cases: "glass", "series", "dress", "chess"

---

## Phase 4: Robustness

### 4.1 Narrow broad `except Exception` blocks

There are 22 `except Exception` blocks in production code. Not all need fixing — some are intentional durability patterns. Address the worst:

- `core/events.py` `emit_event` — returns `None` for DB corruption AND validation errors. At minimum, log distinctly
- `core/llm.py` `_load_default_config` — returns `None` for any error. Distinguish "table missing" from "DB corrupt"
- `core/goal_parse.py` LLM fallback — logs only `type(exc).__name__`. Log the actual message too for debugging
- `jobs.py` — `IntegrityError` string matching (`"jobs.idempotency_key" not in str(exc)`) is fragile. Consider catching the specific constraint error or checking `exc.args` structure
- Leave intentional durability patterns alone (snapshot persistence, vault writes) but add comments explaining why they're broad

### 4.2 Add encoding to file reads

- `meals/recipes.py` `parse_recipe_note` — add `encoding="utf-8"` to `path.read_text()`
- `finance/report_rendering.py` — add `encoding="utf-8"` to template reads
- Audit other `read_text()` / `open()` calls for missing encoding

### 4.3 Add error wrapping to CSV parsers

- `finance/parsers/generic_csv.py` — wrap `row[column]` access in try/except `KeyError` → `InvalidInputError` with a message naming the missing column
- Same for `finance/parsers/dcu.py` and `finance/parsers/robinhood_gold.py`

### 4.4 Add SQL WHERE clauses to replace load-everything-then-filter

Five locations load all rows and filter in Python. Add date-bounded SQL queries:

- `meals/service.py` `list_meals` — add `WHERE occurred_at >= ? AND occurred_at < ?` for date-filtered queries
- `meals/read_api.py` `get_nutrition_summary` — same
- `training/service.py` `list_sessions` — same
- `training/service.py` `get_progress_summary` — same
- `training/read_api.py` `get_training_summary` — same

### 4.5 Extract magic numbers into named constants

- `core/goal_progress.py` — `0.9` and `0.7` → `GOAL_ON_TRACK_THRESHOLD = 0.9`, `GOAL_OFF_TRACK_THRESHOLD = 0.7`
- `core/goal_detectors.py` — `5_000`, `2_000`, `4`, `2` → named constants
- `meals/nutrition.py` — `1200` → `MIN_CALORIE_FLOOR_KCAL`
- `meals/recommendations.py` — `40.0` → `MAX_PROTEIN_THRESHOLD_GRAMS`
- `training/progression.py` — `3`/`2` → `GOOD_ADHERENCE_SESSIONS`, `LOW_ADHERENCE_SESSIONS`
- `finance/service.py` — `500` limit should import `MAX_SENSITIVE_QUERY_LIMIT` from `finance/server.py`, or move the constant to a shared location

---

## Phase 5: Test Suite Cleanup

### 5.1 Stop reaching into `_tool_manager`

- In `test_end_to_end.py`, `test_finance_server.py`, `test_core_server.py`, `test_goal_parse.py`: replace `server._tool_manager.get_tool(...)` with a stable test harness
- Option A: Add a `get_tool(server, name)` helper in `tests/helpers.py` that encapsulates the private access in one place
- Option B: Add a public `get_tool` method to each server's creation function (preferred if FastMCP supports it)

### 5.2 Fix tautological and weak assertions

- `test_finance_server.py` tool registration tests — instead of checking `tool.name == "the_name_you_asked_for"`, assert the full list of registered tools matches expected
- `test_end_to_end.py` snapshot assertions — assert shape of `signals` (list of dicts with expected keys), not just presence
- `test_detectors.py` — remove registry ordering test (implementation detail); keep behavioral detector tests
- `test_meals_server.py` `test_meal_log_tool` — add assertions on DB state or events, not just `success` flag

### 5.3 Fix the manual try/except anti-pattern

- `test_jobs.py` lines 9–17: replace manual try/except/else with `with pytest.raises(NotFoundError, match=fake_id)`
- Search for other instances of this pattern across tests

### 5.4 Add missing edge case tests

- `test_money.py` — add tests for: whitespace input, empty string, very large values, negative zero
- `test_meals_service.py` — add `match=` to `pytest.raises(InvalidInputError)` calls to verify error messages

### 5.5 Fix `FinanceSeeder.transaction` auto-commit

- In `tests/helpers.py`, `FinanceSeeder.transaction` commits on every insert
- Remove the per-insert commit; let tests control transaction boundaries
- Add an explicit `seeder.commit()` or use the connection's commit in tests that need it

---

## Phase 6: Type Tightening

### 6.1 Type the worst `object | None` / `Any` boundaries

- `core/goal_parse.py` `parse_goal_input` — type `goals` parameter (currently no annotation at all). Should be `list[GoalRecord]`
- `core/goal_parse.py` — type `llm` parameter as `JSONPromptLLM | None` instead of `object | None`
- `transport.py` `run_server` — type `mcp` parameter. If FastMCP doesn't export a type, use a Protocol with `.settings` and `.run()`
- `core/interpretation/context.py` `GoalPromptGoal` — replace `object` fields with actual types (`int`, `str`, etc.)
- `core/server.py` `goal_parse` tool — type `structured_input: dict[str, object]` instead of bare `dict`

### 6.2 Replace `dict[str, Any]` tool responses with TypedDict

Not all 190+ instances need fixing. Start with the most-used:

- Define `ToolResponse = TypedDict("ToolResponse", success=bool, data=Any, error=str | None, error_code=str | None)` in `contracts.py`
- Use it as the return type for `ok()`, `fail()`, `wrap_tool_call()`, `wrap_async_tool_call()`
- This makes the envelope shape explicit at the type level

### 6.3 Fix the `FinanceReadInterface` `Any` returns

- Move data-only return types (`SpendingSummary`, `UncategorizedSummary`, `ImportJobIssue`, `PeriodComparison`, `IncomeSummary`) into a new `finance/read_models.py` (or use the existing `finance/import_models.py`)
- Both `core/models.py` and `finance/read_api.py` import from this shared location
- Replace `Any` return types in `FinanceReadInterface` with the real types

---

## Execution Order

Work in phase order. Each phase is independently valuable and shippable.


| Phase         | Effort    | Risk                        | Commit boundary                                   |
| ------------- | --------- | --------------------------- | ------------------------------------------------- |
| 1. Bugs       | 3–4 hours | Low (isolated fixes)        | One commit per fix or batch small fixes           |
| 2. DRY        | 2–3 hours | Medium (many files touched) | One commit per extraction                         |
| 3. Style      | 1–2 hours | Low (mechanical)            | One big `ruff --fix` commit, then manual cleanups |
| 4. Robustness | 2–3 hours | Low-Medium                  | Group related fixes                               |
| 5. Tests      | 2–3 hours | Low                         | One commit per test file refactored               |
| 6. Types      | 2–3 hours | Low                         | One commit per boundary tightened                 |


**Total: ~15–20 hours of work** spread across multiple sessions.