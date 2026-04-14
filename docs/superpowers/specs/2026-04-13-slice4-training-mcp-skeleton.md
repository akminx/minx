# Slice 4 Training MCP: Concrete Skeleton

**Date:** 2026-04-13  
**Status:** Implemented (skeleton completed and integrated)  
**Depends on:** Slices 1-3 foundations (events, domain pattern, Meals online)

## 1) Goal

Build `minx-training` as a first-class MCP domain for workout planning, workout logging, progression tracking, and training signals that compose into Core snapshots and Hermes workflows.

## 2) Scope (Concrete)

1. Training MCP server with deterministic tools for:
   - exercise library CRUD
   - program block CRUD
   - session log write/read
   - progression summaries
2. SQLite schema and service layer following Finance/Meals patterns.
3. Events:
   - `workout.completed`
   - `training.program_updated`
   - `training.milestone_reached`
4. `TrainingSnapshot` read-model contribution to Core daily snapshot.
5. Initial detectors:
   - adherence trend
   - volume progression
   - recovery risk
6. Cross-domain detector v1:
   - nutrition protein consistency + training adherence correlation.

## 3) Non-Goals (Slice 4)

- No wearable sync integrations.
- No LLM-generated autonomous training changes.
- No image/video coaching analysis.
- No harness-specific code inside MCP servers.

## 4) Proposed Package Shape

- `minx_mcp/training/__main__.py`
- `minx_mcp/training/server.py`
- `minx_mcp/training/service.py`
- `minx_mcp/training/read_api.py`
- `minx_mcp/training/models.py`
- `minx_mcp/training/events.py`
- `minx_mcp/training/progression.py`

## 5) Schema Skeleton

Migration: `012_training.sql` (next sequence)

Core tables:
- `training_exercises`
- `training_programs`
- `training_program_days`
- `training_program_exercises`
- `training_sessions`
- `training_session_sets`
- `training_milestones`

Indexes:
- session time index (`occurred_at`)
- exercise lookup (`normalized_name`)
- program status (`is_active`, `updated_at`)

## 6) MCP Tool Skeleton

Writes:
- `training_exercise_upsert`
- `training_program_upsert`
- `training_session_log`
- `training_program_activate`

Reads:
- `training_exercise_list`
- `training_program_get`
- `training_session_list`
- `training_progress_summary`

## 7) Core Read Model Skeleton

Add `TrainingSnapshot`:
- `date`
- `sessions_logged`
- `total_sets`
- `total_volume_kg`
- `last_session_at`
- `adherence_signal`

Daily snapshot integration:
- add optional `training` field in Core snapshot models
- include in read-model builder and detector context.

## 8) Detector Skeleton

1. `training.adherence_drop`
2. `training.volume_stalled`
3. `training.recovery_risk`
4. `cross.training_nutrition_mismatch` (training consistent, protein consistently low)

## 9) Hermes Harness Integration Skeleton

Planned flow:
1. Hermes calls training tools for program/session state.
2. Hermes calls meals nutrition summary/targets.
3. Hermes asks core daily snapshot for combined insights.
4. Hermes narrates and asks user confirmation before any write-side program edits.

## 10) Test Skeleton

Unit:
- progression math
- adherence detector logic
- event payload validation

Integration:
- training write/read roundtrip
- core snapshot includes training contribution
- cross-domain detector with meals + training fixtures

Server:
- tool registration
- contract errors for invalid input

## 11) Definition of Done (Slice 4)

- `minx-training` server runnable independently.
- schema migration applied idempotently; source/packaged SQL mirrored.
- events registered and validated.
- core snapshot includes training contribution.
- deterministic detectors emit expected signals.
- full test suite green + mypy green.
