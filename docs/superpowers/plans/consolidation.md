# Consolidation Tracker

Source plan: `2026-04-15-consolidation-and-refactor.md`
Last updated: 2026-04-17 (1.8 collapsed to 3 modules)

Status legend:

- `[x]` verified in code / tests
- `[~]` partially complete or intentionally scoped differently from the original plan

## Phase 1: Structural Refactoring

- 1.1 `scoped_connection` extracted and adopted in core flows
- 1.2 `goal_parse.py` split into focused modules
- 1.3 `core/models.py` split into focused model/protocol modules
- 1.4 `BaseService` extracted and adopted by finance/meals/training services
- 1.5 Shared validation helper module extraction (direct tests live in `tests/test_validation.py`)
- 1.6 LLM protocol typing cleanup (no remaining `hasattr` duck-typing checks)
- 1.7 Read interfaces narrowed from `Any` to concrete types (finance, meals, training)
- 1.8 Finance report pipeline collapsed to the target 3 modules: `report_models.py` (data types), `report_builders.py` (SQL aggregation + markdown rendering), `report_orchestration.py` (windowing, vault write, persistence). `reports.py`, `report_persistence.py`, and `report_rendering.py` have been removed and their content absorbed into the remaining modules. Callers and tests (`test_finance_templates.py`, `test_finance_reports.py`) import directly from the surviving modules.
- 1.9 Event upcaster contiguity hardening + registration rules. `_upcast_payload` uses the strict `schema_version < version` semantic and has a dedicated non-idempotent regression test

## Phase 2: Observability

- 2.1 Structured JSON logging setup + domain server bootstrap wiring (direct tests in `tests/test_logging_config.py`)
- 2.2 Tool call logging in wrappers (`tool`, `duration_ms`, `success`, `error_code`). Every `wrap_tool_call` / `wrap_async_tool_call` call site in core/finance/meals/training passes `tool_name=`; caplog-based tests in `tests/test_contracts.py` assert the structured fields on success, contract error, and unexpected error paths for both sync and async wrappers
- 2.3 `health://status` resources added across all 4 servers

## Phase 3: Tooling and CI

- 3.1 Ruff configured and integrated in workflow
- 3.2 GitHub Actions CI pipeline added (`.github/workflows/ci.yml` runs `uv sync`, `ruff check`, `mypy`, `pytest`)
- 3.3 Mypy strictness tightening follow-up (no `Any` return escape hatches remaining in the typed read protocols)

## Phase 4: Testing and Migration Hardening

- 4.1 Real transport E2E MCP smoke tests (`tests/test_transport_e2e.py`, `tests/test_hermes_http_smoke.py`)
- 4.2 Non-additive migration strategy and helper coverage (`add_column_if_missing`, SHA-256 checksum enforcement on `_migrations`)
- 4.3 NL goal capture complexity explicitly acknowledged: module split (`goal_capture_nl`, `goal_capture_llm`, `goal_capture_structured`, `goal_capture_utils`) plus module-level docstring + Known Limitations block in `minx_mcp/core/goal_capture_nl.py`

## Phase 5: Roadmap Re-Scoping

- 5.1 Final roadmap rescope update captured in handoff/roadmap docs