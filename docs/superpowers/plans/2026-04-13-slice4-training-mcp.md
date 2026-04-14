# Slice 4 Training MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-pass `minx-training` MCP domain with deterministic workout CRUD/logging, progression summary, and Core snapshot/detector integration.

**Architecture:** Follow the existing Finance/Meals domain pattern: SQLite migration, dataclasses, service layer, read API, and FastMCP server. Wire training read-model data into Core snapshot and add deterministic training + cross-domain detectors backed by read models only (no harness-specific logic).

**Tech Stack:** Python 3.12, sqlite3, FastMCP, pytest, mypy

---

## File Structure

### Create

- `minx_mcp/schema/migrations/012_training.sql`
- `schema/migrations/012_training.sql`
- `minx_mcp/training/__init__.py`
- `minx_mcp/training/__main__.py`
- `minx_mcp/training/models.py`
- `minx_mcp/training/service.py`
- `minx_mcp/training/server.py`
- `minx_mcp/training/read_api.py`
- `minx_mcp/training/progression.py`
- `minx_mcp/training/events.py`
- `tests/test_training_service.py`
- `tests/test_training_server.py`
- `tests/test_training_read_api.py`
- `tests/test_training_detectors.py`

### Modify

- `minx_mcp/core/models.py`
- `minx_mcp/core/read_models.py`
- `minx_mcp/core/detectors.py`
- `minx_mcp/core/snapshot.py`
- `minx_mcp/core/events.py`
- `minx_mcp/launcher.py`
- `pyproject.toml`
- `tests/test_db.py`
- `tests/test_migration_checksums.py`
- `tests/test_meals_server.py`
- `tests/test_launcher.py`
- `tests/test_detector_metadata.py`
- `tests/test_detectors.py`
- `tests/test_events.py`

## Task 1: Add Migration + Dynamic Migration Tests

- [ ] Write failing tests that assert training tables exist and migration expectations are dynamic (no hardcoded count/last migration).
- [ ] Run focused DB/migration checksum tests and confirm failure before migration is added.
- [ ] Add `012_training.sql` to both packaged/mirror migration directories.
- [ ] Re-run focused DB/migration tests until green.

## Task 2: Add Training Domain Service + Models (TDD)

- [ ] Write failing unit tests for exercise upsert/list, program upsert/get/activate, session log/list, and progression summary.
- [ ] Implement `training/models.py`, `training/progression.py`, and `training/service.py` minimally to satisfy tests.
- [ ] Emit training events (`workout.completed`, `training.program_updated`, `training.milestone_reached`) from service write paths.
- [ ] Re-run training service tests and refactor for clarity.

## Task 3: Add Training MCP Server + Entrypoint (TDD)

- [ ] Write failing server tests for required tool registration and write/read tool contracts.
- [ ] Implement `training/server.py`, `training/__main__.py`, and update `pyproject` script + launcher manifest.
- [ ] Re-run server + launcher tests and make output contract consistent with existing domains.

## Task 4: Add Training Read API + Core Snapshot Integration (TDD)

- [ ] Write failing tests for training read summary and Core snapshot training contribution.
- [ ] Implement `training/read_api.py` and extend core models/read-model builder/context/snapshot to include `TrainingSnapshot`.
- [ ] Re-run snapshot/read-model tests and ensure existing nutrition/finance paths remain stable.

## Task 5: Add Training + Cross-Domain Detectors (TDD)

- [ ] Write failing detector tests for `training.adherence_drop`, `training.volume_stalled`, `training.recovery_risk`, and `cross.training_nutrition_mismatch`.
- [ ] Implement detector functions and register them with deterministic ordering + metadata tags.
- [ ] Re-run detector metadata/detector behavior suites.

## Task 6: Add Event Payload Validation + Final Verification

- [ ] Add tests that validate training event payloads through `emit_event` and unknown/invalid payload handling.
- [ ] Register training payload models in `core/events.py`.
- [ ] Run full verification: `uv run pytest -q`, `uv run mypy`, `git diff --check`.

