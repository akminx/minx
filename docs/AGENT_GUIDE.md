# Agent Guide

For Claude Code, Codex, Hermes, or any other agent that needs to work in this repo.

## Mental model

Minx splits durable systems from conversational systems on a hard line.

| Owns | Lives in | Examples |
|---|---|---|
| **Facts and deterministic logic** | `minx` | Finance import, money math, memory CRUD, FTS, vault sync, render templates, investigation storage |
| **Conversation, tool choice, prose, scheduling** | Hermes harness + `minx-hermes/hermes_loop/` | LLM calls, agent loops, slash commands, final answers |

When the boundary is unclear, ask: *would a different harness need this same logic?* If yes, it belongs in `minx`. If it depends on dialogue context, it belongs in Hermes.

## Three workflows

Whatever question the user asks, it usually maps to one of these:

| User signal | Workflow | Slash command | Loop `kind` |
|---|---|---|---|
| "Why did X happen?" / "explain..." | Investigate | `/minx-investigate` | `investigate` |
| "Plan a..." / "what should I do about..." | Plan | `/minx-plan` | `plan` |
| "What changed?" / "review last week..." | Retro | `/minx-retro` | `retro` |
| "Tell me about <entity>" | Onboard entity | `/minx-onboard-entity` | `onboard` |

All four go through the same agentic loop in `hermes_loop/runtime.py`. They differ only in the `kind` value and the system-prompt framing.

## Canonical commands

```bash
# Configure and bring the Minx servers up from the minx repo
cd /path/to/minx
uv run scripts/configure-openrouter.py --model google/gemini-2.5-flash
./scripts/start_hermes_stack.sh                 # MCP servers on 8000-8003

# Verify each repo from its own checkout
uv run pytest tests/ -q                         # minx
cd /path/to/minx-hermes
PYTHONPATH=. uv run pytest tests/ -q            # minx-hermes

# Drive an investigation (production runner)
uv run scripts/minx-investigate.py --kind investigate \
  --question "..." --max-tool-calls 8 --wall-clock-s 90
```

`scripts/configure-openrouter.py` accepts `--model`; use `google/gemini-2.5-flash` as the recommended example unless deployment config says otherwise. Anything else is non-canonical — call it out in your response if you reach for something else.

## Where to look

| If you need... | Read |
|---|---|
| To get the system running | [RUNBOOK.md](RUNBOOK.md) |
| Implementation status / what shipped | [../STATUS.md](../STATUS.md) |
| Setup reference (env vars, paths) | [../OPERATIONS.md](../OPERATIONS.md) |
| Current architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Architecture history | `docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md` |
| Slice 9 contract | `docs/superpowers/specs/2026-04-19-slice9-agentic-investigations.md` |
| Render template contract | `docs/superpowers/specs/2026-04-29-render-template-registry.md` |
| Memory architecture | `docs/superpowers/specs/2026-04-15-slice6-durable-memory.md` |
| Slice history | [archive/handoff-history.md](archive/handoff-history.md) |

## Where NOT to look

- The `~/.hermes/hermes-agent` directory is the upstream Hermes harness. We do not push to it.
- The `~/.hermes/skills/minx/*` paths are symlinks into the minx-hermes repo — edit the repo, not the runtime symlink target.
- Older `*-handoff.md` files in `docs/archive/` are historical context, not active spec.

## Hard rules (these will catch you)

1. **Render template strings live in the registry.** New literal like `"goal_parse.foo.bar"` in code? Add it to `minx_mcp/core/render_templates.py:RENDER_TEMPLATES`. The registry test will refuse anything else.
2. **Investigation `kind` values are closed.** `{investigate, plan, retro, onboard, other}`. Coining a new one ("onboard_entity") breaks Core's CHECK constraint and the live skill.
3. **Investigation steps are digest-only.** `result_json`, `transcript`, `messages`, `raw_output`, `tool_output`, `result_rows` keys are forbidden in `event_slots`. The runtime loop already refuses them; don't try to slip them past.
4. **Money is integer cents, never floats.**
5. **Secret-shaped values get blocked or redacted before any memory / vault / embedding write.** Don't bypass the secret scanner.
6. **Hard budget caps live in the harness loop.** Core enforces a soft sanity cap (`MINX_MAX_TOOL_CALLS_PER_INVESTIGATION`, default 1000) only as defense in depth. Don't push hard caps into Core.
7. **No raw LLM prose stored as durable user-facing text in Core.** Render templates + slots are the contract. The harness composes prose from them.

## Decision tree for "where does my change go?"

```
Is the change about how tools are called, what the model says, or scheduling?
  └─ Yes → goes in minx-hermes/hermes_loop/ or skills/, NOT minx.
Is the change about durable storage, schema, or deterministic computation?
  └─ Yes → goes in minx.
Does it need both?
  └─ Likely two PRs: a Core-side contract change in minx, then a harness change that consumes it.
```

## When the user says "investigate" / "plan" / "retro"

Reach for [RUNBOOK.md § Real-data smokes](RUNBOOK.md#real-data-smokes-one-domain-at-a-time) and run `scripts/minx-investigate.py` with the appropriate `--kind`. Don't reimplement the loop, don't write a one-off script — that's what the runner is for.

## When the user says "this is broken"

1. Get the failed `investigation_id` from `~/.minx/data/minx.db`:
   ```bash
   sqlite3 ~/.minx/data/minx.db "SELECT id, status, error_message FROM investigations ORDER BY id DESC LIMIT 5;"
   ```
2. Check [RUNBOOK.md § Troubleshooting](RUNBOOK.md#troubleshooting) — most failures are config drift, not bugs.
3. If it's a real bug, write a failing test first (existing tests are at `tests/test_investigations.py`, `tests/test_runtime.py` in hermes_loop). Then fix.

## When the user says "make a new feature"

Before writing code: which side of the boundary does it sit on (see "Mental model")? Then check `docs/superpowers/specs/` — there may already be a spec. If not, propose one before implementing. The render-template-registry / Slice 9 work in this repo is what happens when specs lead implementation cleanly.
