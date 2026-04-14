# Project Handoff

Status as of 2026-04-14: Slices 1 through 4 are implemented. Finance, Meals, Training, and Core MCP domains are online, and the repo is ready for Hermes harness integration/testing on top of the current MCP surface.

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
- Minx Core owns deterministic interpretation (read models, detectors, insight history, snapshots).
- Hermes (or any harness) owns narrative, coaching, conversation flow, and scheduling.

Governing rule: data and deterministic logic live in MCP; conversational policy lives in harness.

## Implemented Slices

| Slice | Status | Outcome |
|---|---|---|
| 1: Event Pipeline + Daily Review | Implemented | Event contracts, finance events/read APIs, core read models and baseline detectors |
| 2: Goals + Deeper Detection | Implemented | Goals CRUD/trajectory and deeper drift-style detection |
| 2.1: Conversational Goals + Trust | Implemented | Structured and natural goal capture path with trust/sensitivity boundaries |
| 2.5: MCP Surface Refactor (+ cleanup) | Implemented | Core MCP tool cleanup (`get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `persist_note`, `goal_parse`, `finance_query`) and cleanup fixes |
| 3: Meals MCP | Implemented | Meals logging/planning, nutrition read model integration, meal/nutrition events and detectors |
| 4: Training MCP Skeleton + Integration | Implemented | Training domain server/service/schema/read API/events/progression, core snapshot/detector integration, harness-start scripts |

## Current MCP Surface (High Level)

- Core tools: `get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `persist_note`
- Goal tools: `goal_parse`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, `goal_archive`
- Finance tools: `finance_query` (+ existing finance domain operations)
- Meals domain: meal logging/planning + nutrition summary flows
- Training domain: exercise/program/session/progress flows

## Hermes Harness Readiness

- Startup helper is available at `scripts/start_hermes_stack.sh` for bringing up Finance/Core/Meals/Training MCP services.
- Slice 4 smoke script exists at `scripts/hermes_slice4_smoke.py` for harness-facing integration checks.

## Canonical Specs And Inputs

- Architecture: [docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md)
- Slice roadmap: [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md)
- Slice 3 handoff: [docs/superpowers/plans/2026-04-13-slice3-nutrition-meals-handoff.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/plans/2026-04-13-slice3-nutrition-meals-handoff.md)
- Slice 3 phase 3 handoff: [docs/superpowers/plans/2026-04-13-slice3-phase3-shopping-list-handoff.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/plans/2026-04-13-slice3-phase3-shopping-list-handoff.md)
- Slice 4 skeleton spec: [docs/superpowers/specs/2026-04-13-slice4-training-mcp-skeleton.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-13-slice4-training-mcp-skeleton.md)

## Verification Workflow

Run these before handoff, PR, or harness integration runs:

```bash
uv run pytest -q
uv run mypy
```

Record the date and command outcome in the current handoff/PR notes, but do not freeze long-term health in this file with fixed counts.
