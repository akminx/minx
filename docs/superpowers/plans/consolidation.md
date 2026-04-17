# Consolidation Tracker

Source plan: `2026-04-15-consolidation-and-refactor.md`
Last updated: 2026-04-17

## Phase 1: Structural Refactoring

- [x] 1.1 `scoped_connection` extracted and adopted in core flows
- [x] 1.2 `goal_parse.py` split into focused modules
- [x] 1.3 `core/models.py` split into focused model/protocol modules
- [x] 1.4 `BaseService` extracted and adopted by finance/meals/training services
- [x] 1.5 Shared validation helper module extraction
- [x] 1.6 LLM protocol typing cleanup (remove duck-typing checks)
- [x] 1.7 `FinanceReadInterface` Any return types replaced with concrete types
- [x] 1.8 Finance report pipeline collapsed to 3-module target state
- [x] 1.9 Event upcaster contiguity hardening + registration rules

## Phase 2: Observability

- [x] 2.1 Structured JSON logging setup + domain server bootstrap wiring
- [x] 2.2 Tool call logging in wrappers (`tool`, duration, success, error code)
- [x] 2.3 `health://status` resources added across all 4 servers

## Phase 3: Tooling and CI

- [x] 3.1 Ruff configured and integrated in workflow
- [x] 3.2 GitHub Actions CI pipeline added
- [x] 3.3 Mypy strictness tightening follow-up

## Phase 4: Testing and Migration Hardening

- [x] 4.1 Real transport E2E MCP smoke tests
- [x] 4.2 Non-additive migration strategy and helper coverage
- [x] 4.3 NL goal capture complexity explicitly acknowledged by module split/docs

## Phase 5: Roadmap Re-Scoping

- [x] 5.1 Final roadmap rescope update captured in handoff/roadmap docs
