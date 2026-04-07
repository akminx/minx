# Project Handoff

Status as of 2026-04-07: Slice 1 (Event Pipeline + Daily Review) is implemented and verified. The project is ready for Slice 2 (Goals + Deeper Detection).

## Current State

- Local checkout: `main`
- Remote: `origin` -> `https://github.com/akminx/minx.git`
- Slice 1: Implemented (see roadmap doc for implementation notes)
- Slice 2+: Not started

## What Shipped

### Slice 1 stabilization pass

Shared time utilities:

- `minx_mcp/time_utils.py` — single source for UTC timestamp formatting

Core MCP server:

- `minx_mcp/core/server.py` — `daily_review` tool, harness-facing entry point
- `minx_mcp/core/__main__.py` — `python -m minx_mcp.core` runner

Project-wide typing:

- `pyproject.toml` — mypy now covers the full `minx_mcp` package (was 18-file whitelist)

Documentation reconciliation:

- `docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md` — Slice 1 marked implemented with implementation notes documenting spec divergences

### Previously shipped (pre-stabilization)

Core finance/report stabilization:

- `minx_mcp/finance/service.py`
- `minx_mcp/finance/reports.py`
- `minx_mcp/schema/migrations/006_finance_report_lifecycle.sql`
- `schema/migrations/006_finance_report_lifecycle.sql`

Typed internal finance models:

- `minx_mcp/finance/import_models.py`
- `minx_mcp/finance/report_models.py`
- `minx_mcp/finance/importers.py`
- `minx_mcp/finance/dedupe.py`
- parser modules under `minx_mcp/finance/parsers`

Runtime and filesystem hardening:

- `minx_mcp/vault_writer.py`
- `minx_mcp/transport.py`
- `minx_mcp/jobs.py`

Event pipeline and Minx Core:

- `minx_mcp/core/events.py`
- `minx_mcp/core/models.py`
- `minx_mcp/core/read_models.py`
- `minx_mcp/core/detectors.py`
- `minx_mcp/core/review.py`
- `minx_mcp/core/llm.py`
- `minx_mcp/finance/read_api.py`

## Verification

Latest verification:

- command: `.venv/bin/python -m pytest tests/ -q`
- result: `197 passed`
- command: `.venv/bin/python -m mypy`
- result: `Success: no issues found in 44 source files`

## Known Limitations

- This is still a local single-user tool. There is no auth, multi-user coordination, or remote durability story beyond local SQLite and the filesystem.
- Report generation and daily review pipeline are not globally atomic across SQLite and the vault filesystem.
- The LLM provider registry (`_PROVIDER_BUILDERS`) has no registered providers. The fallback (detector-only) path works; wiring a real LLM provider should happen during or before Slice 2.
- Live runtime smoke checks for the Core MCP server are not yet automated.

## Slice 2 Cleanup Queue

Fix early in Slice 2 (before adding goal infrastructure):

- **Make `daily_review` tool async.** `minx_mcp/core/server.py` creates and tears down a new `asyncio` event loop per tool call. If FastMCP supports async tool handlers, make the tool async directly. Otherwise use `asyncio.get_event_loop()` with a fallback to `new_event_loop()` only when no loop is running.
- **Wire at least one real LLM provider.** `_PROVIDER_BUILDERS` in `minx_mcp/core/llm.py` is empty. The fallback path works, but the LLM-enriched path has never been exercised with a real provider. Slice 2 makes the LLM path more important for goal-aware narration.

Fix when convenient (not blocking):

- **Add `tests/conftest.py` with shared test fixtures.** `test_review.py`, `test_read_models.py`, and `test_core_server.py` all independently define seed helpers (`_seed_event`, `_finance_api_with_attention_items`, etc.). Extract common helpers before Slice 2 adds goal-related tests and the duplication grows.
- **Add direct unit tests for `time_utils.py`.** Currently tested indirectly through `test_events.py`. Missing coverage for edge cases: naive timestamps (no tzinfo) passed to `normalize_utc_timestamp`, and timestamps with non-UTC offsets.
- **Add tests for `transport.py`.** The `ValueError` path in `build_transport_config` for unsupported transports is untested. Low risk given the module is 19 lines.
- **Align `FinanceServiceLike` Protocol with actual signature.** `finance/server.py` Protocol omits the `mapping` parameter that `FinanceService.finance_import` accepts. Works at runtime because `mapping` has a default, but it's spec drift.
- **Decide on `document_text.py`.** `extract_text` shells out to a `LiteParse` binary. No tests, no callers. If future infrastructure, mark it as such. If dead code, remove it.

## Suggested Next Step

Begin Slice 2: Goals + Deeper Detection. The event contract, read model builders, detector registry, review pipeline, and Core MCP server are all in place as foundations.