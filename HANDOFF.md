# Project Handoff

Status as of 2026-04-17: Slices 1 through 4 are implemented, and consolidation/code-quality/observability hardening is complete. Slices 6 (Durable Memory) and 8 (Proactive Autonomy) are fully designed and now prioritized as the next implementation work.

Hermes cutover snapshot (2026-04-14):
- Minx MCP ports: finance `8000`, core `8001`, meals `8002`, training `8003`
- Legacy `financehub` and `souschef` are disabled (kept in config for rollback)
- Hermes Minx skills and cron definitions have been rewritten/pinned to Minx MCP tool paths

## Repo And Branch

- Repo: `/Users/akmini/Documents/minx-mcp`
- Branch: `main`
- Stack: Python 3.12, FastMCP, SQLite, Pydantic, pytest, mypy
- Current health: test suite passing and mypy clean (verify via commands below)

## What Is Minx

Minx is a personal Life OS built as MCP servers.

- Domain MCPs own user facts and domain operations (Finance, Meals, Training).
- Minx Core owns deterministic interpretation (read models, detectors, insight history, snapshots, memory).
- Hermes (or any harness) owns narrative, coaching, conversation flow, scheduling, and wiki maintenance.

Governing rule: **data and deterministic logic live in MCP; conversational policy, scheduling, and LLM prose live in harness.**

## Architecture Decisions

Key architectural decisions that govern future work:

1. **Core/Harness split (Slice 2.5)**: Core provides structured data and tools. Harnesses own orchestration, scheduling, notifications, and LLM-generated prose. This applies to both memory (Slice 6) and autonomy (Slice 8).

2. **Memory tiers (Slice 6)**: Three-tier memory: SQLite for structured facts (Tier 1), SQLite for episodic archives (Tier 2), Obsidian vault as a living wiki (Tier 3, LLM Wiki pattern). Core reads the vault and stores facts. Harness writes wiki pages using LLM via Core tools.

3. **Autonomy split (Slice 8)**: Core provides an audit trail (`playbook_runs`), logging/history MCP tools, and a `playbook://registry` resource. Hermes owns scheduling (uses existing cron), playbook execution scripts, confirmation conversations, and wiki maintenance. No scheduling library in Core.

4. **LLM Wiki pattern (Slices 6+8)**: Inspired by Karpathy — Obsidian is the IDE, the LLM is the programmer, the wiki is the codebase. Minx incrementally builds and maintains structured wiki pages in Obsidian rather than re-deriving knowledge on every query.

## Implemented Slices

| Slice | Status | Outcome |
|---|---|---|
| 1: Event Pipeline + Daily Review | Implemented | Event contracts, finance events/read APIs, core read models and baseline detectors |
| 2: Goals + Deeper Detection | Implemented | Goals CRUD/trajectory and deeper drift-style detection |
| 2.1: Conversational Goals + Trust | Implemented | Structured and natural goal capture path with trust/sensitivity boundaries |
| 2.5: MCP Surface Refactor (+ cleanup) | Implemented | Core MCP tool cleanup (`get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `persist_note`, `goal_parse`, `finance_query`) and cleanup fixes |
| 3: Meals MCP | Implemented | Meals logging/planning, nutrition read model integration, meal/nutrition events and detectors |
| 4: Training MCP Skeleton + Integration | Implemented | Training domain server/service/schema/read API/events/progression, core snapshot/detector integration, harness-start scripts |

## Priority Roadmap (Post-Consolidation)

| Item | Status | Spec/Plan |
|---|---|---|
| Consolidation + code quality | Completed (2026-04-17) | [cleanup.md](docs/superpowers/plans/cleanup.md), [consolidation.md](docs/superpowers/plans/consolidation.md) |
| Observability + CI hardening | Completed (2026-04-17) | [consolidation-and-refactor.md](docs/superpowers/plans/2026-04-15-consolidation-and-refactor.md) |
| Slice 6: Durable Memory | Next (designed) | [slice6-durable-memory.md](docs/superpowers/specs/2026-04-15-slice6-durable-memory.md) |
| Slice 8: Proactive Autonomy | Next after Slice 6 (designed) | [slice8-proactive-autonomy.md](docs/superpowers/specs/2026-04-15-slice8-proactive-autonomy.md) |
| Slice 7: Journal MCP | Deferred | Standard CRUD, same pattern as Meals/Training, build when wanted |
| Slice 5: Harness Adaptation | Deferred | One harness (Hermes) today; add harness-specific behavior directly to Core/Hermes as needed |
| Slice 9: Dashboard | Deferred | Independent technology layer, no MCP dependencies |

## Current MCP Surface (High Level)

- Core tools: `get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `persist_note`
- Goal tools: `goal_parse`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, `goal_archive`
- Finance tools: `finance_query` (+ existing finance domain operations)
- Meals domain: meal logging/planning + nutrition summary flows
- Training domain: exercise/program/session/progress flows

### Planned MCP Surface Additions

Slice 6 will add:
- `memory_list`, `memory_get`, `memory_create`, `memory_confirm`, `memory_reject`, `memory_expire`, `get_pending_memory_candidates`

Slice 8 will add:
- `log_playbook_run`, `playbook_history`
- `playbook://registry` MCP resource

## Hermes Harness Readiness

- Startup helper is available at `scripts/start_hermes_stack.sh` for bringing up Finance/Core/Meals/Training MCP services.
- Slice 4 smoke script exists at `scripts/hermes_slice4_smoke.py` for harness-facing integration checks.
- A real Hermes-style streamable HTTP MCP smoke test exists at `tests/test_hermes_http_smoke.py`; it starts all four servers on temporary ports and verifies cross-domain tool calls over `/mcp`.
- Slice 8 will add playbook runner scripts and wiki maintenance to Hermes cron.

## Canonical Specs And Inputs

- Architecture: [docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md](docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md)
- Slice roadmap: [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md)
- Slice 3 handoff: [docs/superpowers/plans/2026-04-13-slice3-nutrition-meals-handoff.md](docs/superpowers/plans/2026-04-13-slice3-nutrition-meals-handoff.md)
- Slice 3 phase 3 handoff: [docs/superpowers/plans/2026-04-13-slice3-phase3-shopping-list-handoff.md](docs/superpowers/plans/2026-04-13-slice3-phase3-shopping-list-handoff.md)
- Slice 4 skeleton spec: [docs/superpowers/specs/2026-04-13-slice4-training-mcp-skeleton.md](docs/superpowers/specs/2026-04-13-slice4-training-mcp-skeleton.md)
- Slice 6 spec: [docs/superpowers/specs/2026-04-15-slice6-durable-memory.md](docs/superpowers/specs/2026-04-15-slice6-durable-memory.md)
- Slice 8 spec: [docs/superpowers/specs/2026-04-15-slice8-proactive-autonomy.md](docs/superpowers/specs/2026-04-15-slice8-proactive-autonomy.md)
- Code quality plan: [docs/superpowers/plans/2026-04-15-code-quality-cleanup.md](docs/superpowers/plans/2026-04-15-code-quality-cleanup.md)
- Consolidation plan: [docs/superpowers/plans/2026-04-15-consolidation-and-refactor.md](docs/superpowers/plans/2026-04-15-consolidation-and-refactor.md)

## Verification Workflow

Run these before handoff, PR, or harness integration runs:

```bash
uv run pytest -q
uv run mypy
uv run ruff check .
```

Record the date and command outcome in the current handoff/PR notes, but do not freeze long-term health in this file with fixed counts.
