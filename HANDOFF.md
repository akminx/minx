# Handoff

This file is the short working handoff for the next development session. For a polished project overview, start with [README.md](README.md). For operating the system, use [OPERATIONS.md](OPERATIONS.md). For implementation status, use [STATUS.md](STATUS.md).

## Current Snapshot

- Current branch: `main`
- Current migration head: `027_investigations.sql`
- Slice 9 is wired through Core and Hermes overlay surfaces. Core owns durable lifecycle/history/re-query storage; Hermes owns tool choice, budget discipline, confirmation UX, and final prose.
- Render template registry shipped (`minx_mcp/core/render_templates.py`); call sites in `investigations.py`, `goal_models.py`, `tools/memory.py` import named constants. New tests in `tests/test_render_template_registry.py` lock the contract.
- Hermes runtime loop shipped at `hermes_loop/runtime.py` in the active overlay worktree below. Production Hermes must adopt this contract or reimplement against `docs/hermes-investigation-runtime-contract.md`.
- Active Hermes overlay worktree: `/Users/akmini/.config/superpowers/worktrees/minx-hermes/codex-hermes-investigation-loop` on branch `codex/hermes-investigation-loop`.
- Latest Hermes-agent reference worktree: `/Users/akmini/.config/superpowers/worktrees/hermes-agent/codex-minx-slice9-latest` on branch `codex/minx-slice9-latest`.
- Live Hermes config in `/Users/akmini/.hermes/config.yaml` exposes `/minx-investigate`, `/minx-plan`, `/minx-retro`, and `/minx-onboard-entity` plus underscore aliases.
- The root handoff is intentionally brief. Historical slice notes live in [docs/archive/handoff-history.md](docs/archive/handoff-history.md).

## Guardrails

- Keep deterministic data and business logic in MCP services.
- Keep scheduling, conversation policy, and LLM prose in Hermes or another harness.
- Investigation steps stored by Core must remain digest-only; do not persist raw tool output.
- Memory embeddings are lifecycle-gated and should only exist for active, unexpired memories.
- Hard budget enforcement (`max_tool_calls`, wall-clock, tool allowlist) lives in the harness loop, not in Core. Core enforces a soft sanity cap (`MINX_MAX_TOOL_CALLS_PER_INVESTIGATION`, default 1000) only as defense in depth.
- New render-template-shaped string literals must be added to `minx_mcp/core/render_templates.py:RENDER_TEMPLATES`; the registry test refuses unregistered ones.

## Before Continuing

Run the normal checks before merging or handing off:

```bash
uv run ruff check minx_mcp tests scripts
uv run mypy minx_mcp
uv run pytest tests/ -x -q
```
