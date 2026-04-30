# Minx MCP

A local-first personal Life OS built as a set of Model Context Protocol servers. Minx turns everyday personal data — finance, meals, training, vault notes — into structured, auditable context that an MCP-capable harness can use for coaching, reflection, planning, and investigation.

The project is intentionally split between durable systems and conversational systems:

- **Domain MCPs** own facts and deterministic domain operations.
- **Minx Core** owns interpretation, memory, read models, detectors, and audit trails.
- **Hermes (or any harness)** owns dialogue, scheduling, LLM prose, and agent loops.

The database knows what happened. The harness decides how to talk about it.

## Where to start

| You are... | Read |
|---|---|
| Setting it up for the first time | [docs/RUNBOOK.md](docs/RUNBOOK.md) |
| An agent (Claude / Codex / Hermes) about to work in this repo | [docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md) |
| Looking up what's implemented | [STATUS.md](STATUS.md) |
| Looking up env vars / paths / maintenance commands | [OPERATIONS.md](OPERATIONS.md) |
| Mid-session handing off to the next session | [HANDOFF.md](HANDOFF.md) |

## What it does

Minx ships four MCP servers:

| Server | Responsibility |
|---|---|
| `minx-finance` | Personal finance imports, categorization, reports, and read APIs |
| `minx-meals` | Meal, pantry, recipe, and nutrition state |
| `minx-training` | Training logs and progression state |
| `minx-core` | Cross-domain interpretation, memory, snapshots, goals, vault sync, playbook + investigation audit |

Plus a harness-side Hermes integration (live in the [minx-hermes](https://github.com/akminx/minx-hermes) repo) that drives Slice 9 agentic investigations through `hermes_loop/runtime.py` — a budget-enforced, tool-allowlisted loop that calls Nemotron-3-Super on OpenRouter and routes tool calls to the four MCP servers.

## Architecture

```mermaid
flowchart LR
    F["Finance MCP"] --> E["Domain events"]
    M["Meals MCP"] --> E
    T["Training MCP"] --> E
    E --> C["Minx Core"]
    C --> R["Read models and snapshots"]
    C --> G["Goals and trajectories"]
    C --> MEM["Durable memory and search"]
    C --> A["Playbook and investigation audit"]
    C --> V["Obsidian vault primitives"]
    R --> H["Hermes loop / harness"]
    G --> H
    MEM --> H
    A --> H
    V --> H
    H --> LLM["OpenRouter (Nemotron, no-logging providers)"]
```

Core exposes structured facts, not final coaching prose. That makes the system inspectable, testable, and resilient when the LLM layer changes.

## 60-second quick start

```bash
git clone https://github.com/akminx/minx ~/Documents/minx-mcp
cd ~/Documents/minx-mcp
uv sync --all-extras
uv run pytest tests/ -x -q                       # 1165 tests, ~70s

export OPENROUTER_API_KEY=sk-or-v1-...
export MINX_OPENROUTER_API_KEY=$OPENROUTER_API_KEY
uv run scripts/configure-openrouter.py
./scripts/start_hermes_stack.sh                  # MCP servers on 8000-8003
```

Then drive an investigation (from the [minx-hermes](https://github.com/akminx/minx-hermes) checkout):

```bash
uv run scripts/minx-investigate.py --kind investigate \
  --question "what merchants did I spend the most at last month?" \
  --max-tool-calls 6 --wall-clock-s 60
```

For everything else — real-data smokes, troubleshooting, observability — see [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Repo layout

```
minx_mcp/
  core/        Cross-domain interpretation, memory, goals, vault sync, audits, render templates
  finance/     Finance import / read / report tools
  meals/       Meal, pantry, recipe, nutrition tools
  training/    Workout and progression tools
  schema/      Packaged SQLite migrations (single source of truth)
scripts/       Setup, maintenance, smoke helpers
tests/         1165 tests covering domain services, migrations, MCP tools, transports, regressions
docs/
  RUNBOOK.md   How to run end to end
  AGENT_GUIDE.md How agents should think about this repo
  superpowers/specs/   Per-slice specs (architecture decisions)
  archive/     Historical handoffs
```

## Engineering patterns worth borrowing

- Local-first SQLite with explicit, versioned migrations.
- Hard domain boundaries between finance, meals, training, and core interpretation.
- MCP tool contracts that always return structured success/error envelopes.
- Append-only render template registry that prevents Core ↔ harness drift.
- Digest-only audit trails for autonomous workflows (no raw tool output stored).
- Money as integer cents, never floats.
- Secret scanning before any memory / vault / embedding write.
- Test-driven hardening of concurrency, lifecycle, and validation edge cases.

## Limitations

- Local single-user tool. No auth, multi-user isolation, or remote durability.
- SQLite + filesystem writes are recoverable but not globally atomic across resources.
- LLM-backed features degrade to deterministic local behavior when no provider is configured.
- Hermes-side agent loops live in the [minx-hermes](https://github.com/akminx/minx-hermes) repo. Core only stores their durable surfaces.
