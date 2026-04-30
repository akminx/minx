# Handoff

This file is a short, mutable working note for the next session. It is **not** a doc to navigate by — start at [README.md](README.md) and [docs/RUNBOOK.md](docs/RUNBOOK.md).

For polished status, see [STATUS.md](STATUS.md). For operating the system, see [OPERATIONS.md](OPERATIONS.md). Historical slice notes live in [docs/archive/handoff-history.md](docs/archive/handoff-history.md).

## Current snapshot

- Branch: `main`
- Migration head: `027_investigations.sql`
- Slice 9 wired end to end. Core owns durable lifecycle / history / re-query storage; Hermes (`hermes_loop/`) owns tool choice, budget discipline, confirmation UX, final prose.
- Render template registry shipped (`minx_mcp/core/render_templates.py`); the registry test refuses unregistered template-shaped literals across `minx_mcp/core/`.
- Hermes runtime + production runner live at `scripts/minx-investigate.py` in the [minx-hermes](https://github.com/akminx/minx-hermes) repo (active branch `codex/hermes-investigation-loop`). The four `/minx-*` SKILL.md files invoke it.
- LLM and embeddings: OpenRouter for both. Run `scripts/configure-openrouter.py` once to write the `core/llm_config` preference (defaults to Nemotron-3-Super-120B-A12B with `data_collection: deny`).

## Guardrails

- Deterministic data and business logic stays in MCP services.
- Scheduling, conversation policy, and LLM prose stays in Hermes / harness.
- Investigation steps in Core are digest-only; raw tool output never persisted.
- Memory embeddings are lifecycle-gated; only active, unexpired memories.
- Hard budget enforcement (`max_tool_calls`, wall-clock, tool allowlist) lives in the harness loop. Core enforces a soft sanity cap (`MINX_MAX_TOOL_CALLS_PER_INVESTIGATION`, default 1000) only as defense in depth.
- New render-template-shaped literals must be added to `minx_mcp/core/render_templates.py:RENDER_TEMPLATES`; the registry test refuses unregistered ones.

## Before continuing

```bash
# minx-mcp
uv run ruff check minx_mcp tests scripts
uv run mypy minx_mcp
uv run pytest tests/ -x -q

# minx-hermes (worktree path; substitute your own checkout)
PYTHONPATH=$PWD uv run pytest tests/ -x -q
```

## Open punch list

1. Real-data smokes (finance / meals / training / drift / budget exhaustion) — see [docs/RUNBOOK.md § Real-data smokes](docs/RUNBOOK.md#real-data-smokes-one-domain-at-a-time). First pass will surface real bugs unit tests cannot predict.
2. Dashboard (read-only first; Core read APIs already exist).
3. Eval scenarios as record-and-replay pytest cases so model swaps don't silently regress.
4. Slice 7 Ideas / Journal once observability is solid.
