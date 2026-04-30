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
- Live Hermes overlay skills for `/minx-investigate`, `/minx-plan`, `/minx-retro`, `/minx-onboard-entity`, all driven end-to-end by the production runner `scripts/minx-investigate.py` in the minx-hermes repo. Stack: budget-enforcing agentic loop (`hermes_loop/runtime.py`), OpenAI tool-calling policy (`hermes_loop/policies.py`) on Nemotron-3-Super-120B-A12B via OpenRouter with `data_collection: deny` no-logging routing, MCP fan-out dispatcher and Core client (`hermes_loop/mcp_clients.py`) over `streamablehttp_client`.
- LLM provider config: OpenRouter for both chat (Nemotron-3-Super, no-logging providers, fp8/bf16 only, reasoning_effort: medium) and embeddings (`openai/text-embedding-3-small`, `OpenRouterEmbedder` shipped in `memory_embeddings.py:69`). One-shot setup: `uv run scripts/configure-openrouter.py`.

## Current Architecture Boundary

Core stores durable state and exposes structured contracts. Hermes or another MCP harness owns:

- User-facing prose.
- Scheduling and cron behavior.
- Agent loops and tool-choice policy.
- Confirmation UX for risky actions.

Do not move harness responsibilities into Core unless the architecture is intentionally revised.

## Next Work

1. Run the system with real data: import statements, sync vault recipes, log workouts, drive `/minx-investigate` against actual questions. First pass will surface real-world bugs that unit tests cannot predict.
2. Build dashboard/inspection surfaces for goals, memories, playbooks, and investigations (lightweight read-only first; mutating actions stay in MCP/Hermes).
3. Add repeatable eval scenarios (dining spend drift, goal drift, memory context, budget exhaustion) so regressions are caught between LLM/model swaps.
4. Continue toward Slice 7 Ideas/Journal once investigation observability is understandable.

## Known Limitations

- Local single-user tool: no auth, multi-user coordination, or remote durability.
- SQLite plus filesystem operations are recoverable but not globally atomic.
- Optional LLM/embedding paths depend on provider configuration and must keep deterministic fallbacks.
- Some historical database rows may need one-time maintenance commands listed in [OPERATIONS.md](OPERATIONS.md).
