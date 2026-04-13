# Slice 4 Training MCP + Hermes End-to-End Handoff

## Current State (as of 2026-04-13)

Slice 3 Phase 3 + Phase 4 work is complete on branch `codex/slice3-shopping-phase4`.

Commits:
- `1f66457` feat(meals): add shopping lists and richer recipe details
- `3382207` fix(meals): harden artifact rollback and shopping list rendering

Branch is pushed to origin:
- `origin/codex/slice3-shopping-phase4`

PR creation via connector is blocked by permissions (`403 Resource not accessible by integration`), so create PR manually:
- https://github.com/akminx/minx/pull/new/codex/slice3-shopping-phase4

Validation already completed on this branch:
- `uv run pytest -q` -> `515 passed`
- `uv run pytest tests/test_meals_*.py -q` -> `36 passed`
- `uv run mypy` -> success
- `git diff --check` -> clean

---

## Next Objective

Implement **Slice 4: Training MCP**, then wire and validate with **Hermes harness** in a full end-to-end flow.

Roadmap reference:
- `docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md` (Slice 4 section)

Slice 4 scope from roadmap:
- Training domain MCP server for workout plans, exercise library, session logs, progression
- Training events (`workout.completed`, `training.program_updated`, `training.milestone_reached`)
- Core `TrainingSnapshot` contribution and training detectors
- Cross-domain detectors (training + nutrition, training + spending)

---

## Recommended Execution Order

### 1) Create Slice 4 spec + implementation plan

Create:
- `docs/superpowers/specs/2026-04-13-slice4-training-mcp-design.md`
- `docs/superpowers/plans/2026-04-13-slice4-training-mcp.md`

Keep it parallel to Meals architecture and avoid broad abstraction work.

### 2) Implement Training MCP foundation

Expected new package shape:
- `minx_mcp/training/__init__.py`
- `minx_mcp/training/__main__.py`
- `minx_mcp/training/models.py`
- `minx_mcp/training/service.py`
- `minx_mcp/training/server.py`
- `minx_mcp/training/read_api.py`
- `minx_mcp/training/events.py`

Migration:
- `minx_mcp/schema/migrations/012_training.sql`
- `schema/migrations/012_training.sql`

Likely launcher/script update:
- add `minx-training` entrypoint in `pyproject.toml`
- include training server in launcher manifest if needed

### 3) Integrate Training read path into Core

Likely touch points:
- `minx_mcp/core/models.py`
- `minx_mcp/core/read_models.py`
- `minx_mcp/core/snapshot.py`
- `minx_mcp/core/detectors.py`

Add Training snapshot assembly and at least initial deterministic training detectors.

### 4) Hermes harness hookup

After Training MCP exists, point Hermes to local MCP servers:
- `minx-core`
- `minx-finance`
- `minx-meals`
- `minx-training`

If Hermes lives in another repo/workspace, do that setup there and keep Minx-side changes minimal.

### 5) End-to-end real usage test

Run a realistic user flow in Hermes:
1. Log a meal and pantry/recipe operation
2. Log a training session
3. Query daily snapshot/context
4. Request recommendation + explicit shopping list generation path
5. Verify cross-domain signal output in harness response

Record actual observed outputs and gaps, then file follow-up issues.

---

## Definition of Done for this handoff

For Slice 4 + Hermes E2E completion:
- Training MCP tools run via FastMCP and pass unit/server tests
- Core snapshot includes Training contribution
- at least one training detector and one cross-domain detector are covered by tests
- Hermes can call all four domain servers in one session
- one documented end-to-end user scenario works without manual DB edits
- full repo checks pass (`pytest`, `mypy`, `git diff --check`)

---

## Notes / Risks

- Do not move business logic into Hermes; keep it in domain MCP/Core.
- Keep Training implementation conservative and test-first, matching existing Finance/Meals patterns.
- If Hermes setup is blocked by external repo permissions/config, finish Slice 4 and leave a precise integration checklist rather than partial ad-hoc wiring.
