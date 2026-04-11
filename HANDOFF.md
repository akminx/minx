# Project Handoff

Status as of 2026-04-11: Slices 1 through 2.5 are **implemented and on main**, including the Slice 2.5 cleanup (bug fix, refactor, 61 new tests). The codebase is ready for Slice 3: Meals MCP.

## Repo And Branch

- Repo: `/Users/akmini/Documents/minx-mcp`
- Branch: `main`
- Stack: Python 3.12, FastMCP, SQLite, Pydantic, pytest, OpenAI-compatible LLM path
- Tests: 392 passing, mypy clean on 60 source files

## What Is Minx

A personal Life OS built as a set of MCP servers. Domain MCPs own facts (Finance today, Meals and Training planned). Minx Core owns interpretation — it consumes domain events, runs deterministic detectors, and exposes structured data for any MCP-capable harness to consume. The harness (Hermes today) owns narrative, coaching, conversation, and scheduling.

**The governing principle:** If logic depends on user data or goals → MCP. If logic depends on how you talk to the user → harness.

## What Has Been Built

| Slice | Status | What It Delivered |
|---|---|---|
| 1: Event Pipeline + Daily Review | Implemented | Event contract, Finance MCP events, Finance read API, Core read models, 2 detectors, review pipeline |
| 2: Goals + Deeper Detection | Implemented | Goals CRUD, goal progress, goal drift + category drift detectors, OpenAI-compatible LLM path |
| 2.1: Conversational Goals + Trust | Implemented | Transport-agnostic goal capture, sensitivity policy, protected review boundary |
| 2.5: MCP Surface Refactor | Implemented | `get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `persist_note`, `goal_parse` dual-path, `finance_query` dual-path, narrative/vault/LLM-review removed from Core |
| 2.5 Cleanup | Implemented | Merchant bug fix, `goal_capture.py` → `goal_parse.py` refactor complete, slope-based trend, 61 new tests (392 total) |

## Current Core MCP Tools

| Tool | Purpose |
|---|---|
| `get_daily_snapshot` | Structured read models + detector signals + attention items. No narrative. |
| `get_insight_history` | Historical detector signals with recurrence counts |
| `get_goal_trajectory` | Goal progress across periods with trend (improving/worsening/stable) |
| `persist_note` | Generic vault write for harness-generated content |
| `goal_parse` | Dual-path: natural language or structured input → create/update/clarify |
| `goal_create/list/get/update/archive` | Goal CRUD |
| `finance_query` | Dual-path: structured filters or natural language → transactions/spending/counts |

## What To Do Next

**Slice 3: Meals MCP** — the first second domain. This validates the cross-domain architecture.

Scope from the roadmap:
- Meals domain MCP server: meal logs, foods/ingredients, recipes, nutrition facts
- Service layer, SQLite schema, MCP tools following the Finance pattern
- Event emission: `meal.logged`, `nutrition.day_updated`, `meal.plan_updated`
- NutritionSnapshot read model in Minx Core
- Meals-specific detectors: nutrition gaps, protein tracking, meal frequency
- Cross-domain detectors: restaurant spend vs meal prep days
- DailySnapshot updated to include nutrition context

No spec exists yet for Slice 3. The first step is to write one, using the architecture design and the Finance MCP as the template.

## Canonical Design Inputs

- Architecture design: [docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md)
- Roadmap slices: [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md)
- Slice 2.5 spec (current surface): [docs/superpowers/specs/2026-04-10-slice2.5-mcp-surface-refactor-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-10-slice2.5-mcp-surface-refactor-design.md)
- Finance MCP (template for new domains): `minx_mcp/finance/`

## Key Architecture Decisions

- **MCP/harness split:** MCP owns data + deterministic signals. Harness owns narrative, coaching, scheduling.
- **DailySnapshot (not DailyReview):** Structured data, no narrative, no vault write, no LLM review.
- **Dual-path inputs:** Both `goal_parse` and `finance_query` accept structured or natural language input.
- **Event-driven integration:** Domains emit events; Core consumes them for cross-domain insights.
- **Hybrid intelligence:** Deterministic detectors generate candidates; LLM contextualizes (when configured).

## Verification

```bash
.venv/bin/python -m mypy           # 0 issues on 60 source files
.venv/bin/python -m pytest -q      # 392 passed
```
