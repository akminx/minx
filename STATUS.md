# Project Status

Last updated: 2026-04-29

Minx MCP is implemented through Slice 9 investigation storage/read/re-query surfaces. The system has four active MCP servers: Finance, Core, Meals, and Training.

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
- Investigation lifecycle/history storage with digest-only steps, structured citations, and prior-investigation references from `memory_list(include_cited_investigations=true)`.
- Render template registry (`minx_mcp/core/render_templates.py`) — 18 IDs covering finance_query, goal_parse, memory_capture, and investigation surfaces. Fulfills the MCP render contract and template-registry specs.
- Soft tool-call cap in `append_investigation_step` (`MINX_MAX_TOOL_CALLS_PER_INVESTIGATION`, default 1000) as defense in depth against runaway harnesses.
- Bounded `memory_list(include_cited_investigations=true)` (last 200 investigations, max 20 citations per memory).
- Live Hermes overlay skills for `/minx-investigate`, `/minx-plan`, `/minx-retro`, `/minx-onboard-entity`, plus a budget-enforcing reference runtime loop in the minx-hermes repo (`hermes_loop/runtime.py`) with hard `max_tool_calls` / wall-clock caps, a read-only tool allowlist, and terminal-status guarantee.

## Current Architecture Boundary

Core stores durable state and exposes structured contracts. Hermes or another MCP harness owns:

- User-facing prose.
- Scheduling and cron behavior.
- Agent loops and tool-choice policy.
- Confirmation UX for risky actions.

Do not move harness responsibilities into Core unless the architecture is intentionally revised.

## Next Work

1. Add repeatable smoke/eval scenarios for dining spend, goal drift, memory context, and budget exhaustion.
2. Add lightweight health views for stuck or failed playbooks and investigations.
3. Build richer dashboard/inspection surfaces for goals, memories, playbooks, and investigations.
4. Continue toward Slice 7 Ideas/Journal once investigation observability is understandable.

## Known Limitations

- Local single-user tool: no auth, multi-user coordination, or remote durability.
- SQLite plus filesystem operations are recoverable but not globally atomic.
- Optional LLM/embedding paths depend on provider configuration and must keep deterministic fallbacks.
- Some historical database rows may need one-time maintenance commands listed in [OPERATIONS.md](OPERATIONS.md).
