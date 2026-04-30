# Handoff

Short-lived working note for the next session. This is not the architecture source of truth; start with `README.md`, `STATUS.md`, `docs/ARCHITECTURE.md`, and `docs/RUNBOOK.md` for current behavior.

## Current Snapshot

- Branch: `main`
- Migration head: `027_investigations.sql`
- Core owns durable MCP state, render hints, memory, vault primitives, playbook audit, and investigation audit/history.
- Hermes / another harness owns dialogue, scheduling, tool-choice policy, confirmation UX, and final prose.
- The Hermes production investigation runner is live in the `minx-hermes` repo through `scripts/minx-investigate.py` and `hermes_loop/`.
- Model selection is deployment configuration. Current setup examples use `google/gemini-2.5-flash` on OpenRouter.

## Guardrails

- Keep deterministic business logic in MCP services.
- Keep agent loops, budget enforcement, and user-facing prose in the harness.
- Store investigation steps as digests and summaries only; do not persist raw tool output.
- Store API keys in environment variables only. Preferences may store env-var names, never key values.
- Add new render semantics by minting a new template id in `minx_mcp/core/render_templates.py`.

## Verification Before Handoff

```bash
uv run ruff check minx_mcp tests scripts
uv run mypy minx_mcp
uv run pytest tests/ -q
```

If you have `minx-hermes` checked out, also run its test suite from that repo.

If `uv` is not available in the shell, use the project environment's `ruff`, `mypy`, and `pytest` directly, but fix PATH before presenting the project.

## Open Punch List

1. Run real-data smokes: finance, meals, training, goal drift, and budget exhaustion.
2. Add repeatable eval scenarios for LLM/model swaps.
3. Build read-only dashboard/inspection surfaces after smokes are boring.
4. Continue Slice 7 Ideas / Journal once investigation observability is stable.
