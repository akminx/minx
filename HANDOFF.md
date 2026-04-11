# Project Handoff

Status as of 2026-04-11: Slice 2.5 (MCP Surface Refactor) is **implemented and on main**. A thorough review found five issues (one bug, one incomplete refactor, one weak algorithm, test gaps, stale docs). The cleanup spec is ready to execute before starting Slice 3.

## Repo And Branch

- Repo: `/Users/akmini/Documents/minx-mcp`
- Branch: `main`
- Stack: Python 3.12, FastMCP, SQLite, Pydantic, pytest, OpenAI-compatible LLM path
- Tests: 331 passing, mypy clean on 61 source files

## What To Do Next

**Execute the Slice 2.5 cleanup spec:** [docs/superpowers/specs/2026-04-11-slice2.5-cleanup-spec.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-11-slice2.5-cleanup-spec.md)

This spec has 7 tasks in priority order:

| # | Task | Priority | Summary |
|---|---|---|---|
| 1 | Fix merchant canonicalization bug | P0 | `goal_parse.py:291` — resolve path rejects valid normalized merchants |
| 2 | Complete `goal_capture.py` → `goal_parse.py` refactor | P1 | Move functions, delete old file, remove private cross-module imports |
| 3 | Add tests for goal NL parsing | P1 | Zero dedicated coverage on 742 lines of branchy NLU logic |
| 4 | Improve `_compute_trend` | P2 | Replace first-vs-last with slope-based trend detection |
| 5 | Fill remaining test gaps | P2 | analytics, report validation, interpretation context, import detection |
| 6 | Update stale doc references | P2 | Roadmap still says `DailyReview` in Slices 3/4/7 |
| 7 | Delete `.tmp-does-not-exist` | P3 | Stray empty file in repo root |

**Execution order:** Tasks 1→2→3 are sequential (each depends on the prior). Tasks 4-7 are independent.

Read the full spec — it has exact file paths, line numbers, code examples, and acceptance criteria for each task.

## After Cleanup: Slice 3 Readiness

Once the cleanup lands, the codebase is ready for **Slice 3: Meals MCP** (the first second domain). Slice 3 scope from the roadmap:

- Meals domain MCP server: meal logs, foods/ingredients, recipes, nutrition facts
- Service layer, SQLite schema, MCP tools following the Finance pattern
- Event emission: `meal.logged`, `nutrition.day_updated`, `meal.plan_updated`
- NutritionSnapshot read model in Minx Core
- Meals-specific detectors: nutrition gaps, protein tracking, meal frequency
- Cross-domain detectors: restaurant spend vs meal prep days
- DailySnapshot updated to include nutrition context

Slice 3 will be the first real validation of the cross-domain architecture.

## Canonical Design Inputs

- **Cleanup spec (EXECUTE THIS):** [docs/superpowers/specs/2026-04-11-slice2.5-cleanup-spec.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-11-slice2.5-cleanup-spec.md)
- Slice 2.5 spec (implemented): [docs/superpowers/specs/2026-04-10-slice2.5-mcp-surface-refactor-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-10-slice2.5-mcp-surface-refactor-design.md)
- Architecture design: [docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md)
- Roadmap slices: [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md)

## Key Architecture Decisions

- **MCP/harness split:** MCP owns data + deterministic signals + temporal history. Harness owns narrative, coaching, conversation, scheduling. This is the governing principle — do not add narrative generation, markdown rendering, or scheduling logic to Core.
- **DailySnapshot replaces DailyReview:** Structured data, no narrative, no vault write, no LLM review. Harness generates narrative from snapshot data.
- **Dual-path inputs:** Both `goal_parse` and `finance_query` accept structured input (for harness-driven deterministic paths) or natural language (for user-facing conversational paths).

## Verification Commands

```bash
.venv/bin/python -m mypy           # must report 0 issues
.venv/bin/python -m pytest -q      # must report 0 failures
```
