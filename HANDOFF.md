# Handoff

This file is the short working handoff for the next development session. For a polished project overview, start with [README.md](README.md). For operating the system, use [OPERATIONS.md](OPERATIONS.md). For implementation status, use [STATUS.md](STATUS.md).

## Current Snapshot

- Current branch: `main`
- Current migration head: `027_investigations.sql`
- Primary next work: Hermes-side Slice 9d, the real `minx_investigate` loop outside this repository.
- Core-side Slice 9 storage/read APIs are implemented; Core does not own the runtime agent loop.
- The root handoff is intentionally brief. Historical slice notes live in [docs/archive/handoff-history.md](docs/archive/handoff-history.md).

## Guardrails

- Keep deterministic data and business logic in MCP services.
- Keep scheduling, conversation policy, and LLM prose in Hermes or another harness.
- Investigation steps stored by Core must remain digest-only; do not persist raw tool output.
- Memory embeddings are lifecycle-gated and should only exist for active, unexpired memories.

## Before Continuing

Run the normal checks before merging or handing off:

```bash
uv run ruff check minx_mcp tests scripts
uv run mypy minx_mcp
uv run pytest tests/ -x -q
```
