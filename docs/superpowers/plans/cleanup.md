# Cleanup Tracker

Source plan: `2026-04-15-code-quality-cleanup.md`
Last updated: 2026-04-17

## Phase 1: Actual Bugs

- [x] 1.1 MealsService `__new__` hack removed (`from_connection` path added)
- [x] 1.2 Training display-name casing preservation
- [x] 1.3 Training coercion errors normalized to `InvalidInputError`
- [x] 1.4 `IncomeSource` model introduced for income summaries
- [x] 1.5 Discover parser 2-digit/4-digit year handling fixed
- [x] 1.6 `goals.list_goals(status=None)` returns unfiltered goals
- [x] 1.7 Jobs stuck-job retry fall-through clarified with explicit comment
- [x] 1.8 `document_text.py` subprocess timeout added

## Phase 2: Copy-Paste Elimination

- [x] 2.1 Shared timezone/day-boundary utilities moved to `time_utils.py`
- [x] 2.2 Shared `next_day` utility adoption across finance modules
- [x] 2.3 Test helper consolidation (`call_tool_sync`, shared test config)
- [x] 2.4 Finance import validation deduplicated
- [x] 2.5 Goal metric dispatch helper extraction (`sum_*` vs `count_*`)
- [x] 2.6 `wrap_tool_call` and `wrap_async_tool_call` internals deduplication
- [x] 2.7 Shared entrypoint helper extracted (`minx_mcp/entrypoint.py`)

## Phase 3: Style and Slop Cleanup

- [x] 3.1 Import ordering and ruff cleanup pass
- [x] 3.2 Logger naming standardized
- [x] 3.3 Dead wrapper/unused symbol cleanup fully complete
- [x] 3.4 Useless comments removed (including parser `__init__`)
- [x] 3.5 Money precision/display API clarified (`cents_to_display_dollars`)
- [x] 3.6 Pantry singularization exceptions added for false positives

## Phase 4: Robustness

- [x] 4.1 Broad `except` handling narrowed/improved in priority sites
- [x] 4.2 Explicit UTF-8 encoding for template/file reads
- [x] 4.3 CSV parser error wrapping for missing columns
- [x] 4.4 SQL date-window filtering replaces load-all + Python filtering
- [x] 4.5 Magic-number extraction complete across all targeted modules

## Phase 5: Test Suite Cleanup

- [x] 5.1 `tests/helpers.py::get_tool` wrapper introduced
- [x] 5.2 Weak/tautological assertions fully upgraded
- [x] 5.3 Manual try/except anti-pattern cleanup in jobs tests
- [x] 5.4 Added message matching coverage for key `InvalidInputError` paths
- [x] 5.5 `FinanceSeeder.transaction` no longer auto-commits each insert

## Phase 6: Type Tightening

- [x] 6.1 Core protocol/annotation tightening fully complete
- [x] 6.2 `ToolResponse` TypedDict adoption in contracts wrapper surface
- [x] 6.3 `FinanceReadInterface` Any-return removal/circular import resolution
