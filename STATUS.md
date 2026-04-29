# Project Status

Last updated: 2026-04-29

Minx MCP is implemented through the Core-side Slice 9 investigation storage/read surface. The system has four active MCP servers: Finance, Core, Meals, and Training.

## Implemented

### Finance

- CSV/PDF import workflow with staging-root restrictions.
- Idempotent imports and recoverable job lifecycle.
- Integer-cent money storage and explicit report rendering.
- Account, category, merchant, spending, and income read APIs.
- Weekly and monthly report generation.
- Sensitive finance query audit envelopes.

### Meals

- Pantry item storage and read APIs.
- Recipe parsing, indexing, and vault reconciliation.
- Meal logging and nutrition-day events.
- Nutrition profiles and recommendation helpers.

### Training

- Workout logging and read APIs.
- Progression helpers and training events.
- Cross-domain signals that can feed Core snapshots.

### Core

- Daily snapshots, insight history, and goal trajectories.
- Goal CRUD, progress, drift signals, and natural-language parsing.
- Durable memory CRUD, candidate review, capture, FTS5 search, graph edges, and optional embedding rerank.
- Secret scanning for memory and vault-write surfaces.
- Vault scan/reconcile/write primitives for Obsidian-style notes.
- Enrichment queue for background memory work.
- Playbook audit/history tools.
- Investigation lifecycle/history storage with digest-only steps.

## Current Architecture Boundary

Core stores durable state and exposes structured contracts. Hermes or another MCP harness owns:

- User-facing prose.
- Scheduling and cron behavior.
- Agent loops and tool-choice policy.
- Confirmation UX for risky actions.

Do not move harness responsibilities into Core unless the architecture is intentionally revised.

## Next Work

1. Implement the real Hermes-side `minx_investigate` loop.
2. Build a safe read-first tool catalog for investigations.
3. Enforce investigation budgets for tool calls, wall clock, and large outputs.
4. Add repeatable smoke/eval scenarios for dining spend, goal drift, memory context, and budget exhaustion.
5. Add lightweight health views for stuck or failed playbooks and investigations.

## Known Limitations

- Local single-user tool: no auth, multi-user coordination, or remote durability.
- SQLite plus filesystem operations are recoverable but not globally atomic.
- Optional LLM/embedding paths depend on provider configuration and must keep deterministic fallbacks.
- Some historical database rows may need one-time maintenance commands listed in [OPERATIONS.md](OPERATIONS.md).
