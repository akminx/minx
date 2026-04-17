# Project Handoff

Status as of 2026-04-17: Slices 1 through 4 are implemented, consolidation/code-quality/observability hardening is complete, and **Slice 6 phases 6a + 6b (durable memory schema, service, MCP tools, first detectors, and snapshot archiving) are shipped on `main`**. Follow-up hardening also landed on `main`: the redundant `schema/migrations` repo-root mirror was removed (single source of truth is `minx_mcp/schema/migrations/`), finance report templates were packaged correctly so wheel installs render reports (prior pathing only worked in editable installs), and Meals shipped a `recipe-starter.md` scaffold plus a `recipe_template` MCP tool so users and the harness can author indexer-compatible recipe notes from a known-good starting point. Slice 6 phases 6c–6f (vault scanner + index, `MemoryContext` on snapshots, vault-write MCP tool, bidirectional vault↔SQLite sync) are next. Slice 8 (Proactive Autonomy) is fully designed and queued after Slice 6.

Hermes cutover snapshot (2026-04-14):
- Minx MCP ports: finance `8000`, core `8001`, meals `8002`, training `8003`
- Legacy `financehub` and `souschef` are disabled (kept in config for rollback)
- Hermes Minx skills and cron definitions have been rewritten/pinned to Minx MCP tool paths

## Repo And Branch

- Remote: `github.com/akminx/minx`
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
| 6a: Memory schema + MemoryService + CRUD tools + first detectors | Implemented (2026-04-17) | `memories` + `memory_events` tables, lifecycle (create/confirm/reject/expire + auto-promote at 0.80), `MemoryService.ingest_proposals` dedupe/merge, three detectors (recurring merchant / category preference / schedule), seven MCP tools |
| 6b: Snapshot archives | Implemented (2026-04-17) | `snapshot_archives` table with content-hash dedupe, `build_daily_snapshot` auto-persist, `list_snapshot_archives` and `get_snapshot_archive` MCP tools |
| 6 hardening | Implemented (2026-04-17) | Partial unique index `UNIQUE(memory_type, scope, subject) WHERE status IN ('candidate','active')`, `expire_memory` restricted to `active`-only (rejection is truly terminal), `create_memory` maps the partial-unique violation to `CONFLICT`, `scope` filter on list/pending tools, `VaultReader` handles UTF-8 BOM, `VaultWriter` raises `InvalidInputError` |
| Packaging + scaffolds hygiene | Implemented (2026-04-17) | Removed redundant `schema/migrations/` repo-root mirror (single source of truth: `minx_mcp/schema/migrations/`); moved finance report templates into `minx_mcp/finance/templates/` and wired them via `[tool.setuptools.package-data]` so wheel installs can render reports; shipped `minx_mcp/meals/templates/recipe-starter.md` as package data + `minx_mcp.meals.templates` loader module (`recipe_starter_template_path`, `read_recipe_starter_template`) + `recipe_template` MCP tool on minx-meals; wheel-packing test `tests/test_db.py::test_built_wheel_includes_packaged_resources` now guards all packaged assets |

## Priority Roadmap (Post-Consolidation)

| Item | Status | Spec/Plan |
|---|---|---|
| Consolidation + code quality | Completed (2026-04-17) | [cleanup.md](docs/superpowers/plans/cleanup.md), [consolidation.md](docs/superpowers/plans/consolidation.md) |
| Observability + CI hardening | Completed (2026-04-17) | [consolidation-and-refactor.md](docs/superpowers/plans/2026-04-15-consolidation-and-refactor.md) |
| Slice 6a–6b: Durable Memory foundations | Shipped 2026-04-17 | [slice6-durable-memory.md](docs/superpowers/specs/2026-04-15-slice6-durable-memory.md) |
| Slice 6c–6f: Vault scanner, MemoryContext, vault-write MCP, bidirectional sync | Next | [slice6-durable-memory.md](docs/superpowers/specs/2026-04-15-slice6-durable-memory.md) |
| Slice 8: Proactive Autonomy | Next after Slice 6 (designed) | [slice8-proactive-autonomy.md](docs/superpowers/specs/2026-04-15-slice8-proactive-autonomy.md) |
| Slice 7: Journal MCP | Deferred | Standard CRUD, same pattern as Meals/Training, build when wanted |
| Slice 5: Harness Adaptation | Deferred | One harness (Hermes) today; add harness-specific behavior directly to Core/Hermes as needed |
| Slice 9: Dashboard | Deferred | Independent technology layer, no MCP dependencies |

## Current MCP Surface (High Level)

- Core tools: `get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `persist_note`
- Goal tools: `goal_parse`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, `goal_archive`
- Finance tools: `finance_query` (+ existing finance domain operations)
- Meals domain: meal logging/planning + nutrition summary flows + `recipe_template` (returns the packaged `recipe-starter.md` scaffold for users/harness to author indexer-compatible recipe notes)
- Training domain: exercise/program/session/progress flows
- **Memory tools (Slice 6a/6b)**: `memory_list(status?, memory_type?, scope?, limit?)`, `memory_get`, `memory_create(memory_type, scope, subject, confidence, payload, source, reason?)`, `memory_confirm`, `memory_reject` (candidate-only), `memory_expire` (active-only), `get_pending_memory_candidates(scope?, limit?)`, `list_snapshot_archives`, `get_snapshot_archive`

### Planned MCP Surface Additions

Slice 6c–6f will add:
- A `vault_index` table + scanner (6c) — no new MCP tool required yet
- `MemoryContext` on `DailySnapshot` (6d) — surfaces through existing `get_daily_snapshot`
- A vault-write MCP tool wrapping `VaultWriter.replace_section` (6e), plus packaged wiki page template scaffolds at `minx_mcp/core/templates/wiki/{entity,pattern,review,goal}.md` and an optional `template://wiki/{page_type}` MCP resource so LLM-generated vault notes have a stable frontmatter + section structure (see Slice 6 spec §9, mirrored by Slice 8 §7)
- Bidirectional vault↔SQLite sync + merge/conflict rules (6f)

Meals Phase 3 (deferred) will add:
- A `minx_mcp/meals/templates/shopping-list.md` `string.Template` scaffold (same packaging pattern as the finance templates and the Phase 6e wiki scaffolds) for deterministic SQL-backed shopping-list renders — see Slice 3 spec §"Phase 3: Shopping List Generation"

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
