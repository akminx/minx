# Slice 9: Agentic Investigations

**Date:** 2026-04-19
**Status:** Designed (not yet implemented; deferred until Slice 6i-6l complete)
**Depends on:** Slice 6 memory retrieval/enrichment foundation, Slice 8 playbook audit pattern establishes the logging shape

## 1) Goal

Give Minx the ability to answer **open-ended questions with unknown tool sequences** — "why did food spending spike this month?", "plan my week around Thursday's deadline", "tell me everything about merchant X". These are one-shot investigations where the LLM decides at each step which tool to call next, not scripted playbooks.

## 2) Scope Boundary — Why This Is Not Slice 8


| Property      | Slice 8 Playbooks                 | Slice 9 Investigations               |
| ------------- | --------------------------------- | ------------------------------------ |
| Trigger       | Cron / event                      | User-initiated (one-off)             |
| Tool sequence | Pre-scripted                      | Chosen by LLM at each step           |
| Cost profile  | Bounded, predictable (N calls)    | Variable, needs per-run budget       |
| Audit shape   | `playbook_runs` row               | `investigations` row with trajectory |
| Failure mode  | Crash mid-script                  | Agent loops / goes off-rails         |
| Output        | Side effects (vault writes, logs) | An answer (optionally persisted)     |


**Rule of thumb:** recurring + predictable → playbook. One-shot + unpredictable → investigation. Recurring + unpredictable is a design smell; split it into a scheduled trigger that fires an investigation.

## 3) Where the Agent Loop Lives

**Harness-side.** Core stays a toolbox.

Reasons:

1. **LLM binding is already harness-side** — Core exposes data and templates; agent loops are just more LLM calls, chosen by the LLM.
2. **Cost/killability is a harness concern** — Hermes sets per-invocation budgets (`max_tool_calls`, `max_tokens`, wall-clock timeout). Core shouldn't know about that.
3. **Trace viewing belongs next to the UI** — users asking "why did Minx do that?" want to scrub a trajectory; that's Hermes' job.
4. **Portability stays intact** — swap the harness and tools still work; only the investigation UX needs to be rebuilt. Correct boundary.

### What Core contributes

- **Durable storage** for investigation records (question + harness-authored answer + trajectory + cost + latest render event).
- **Lifecycle logging tools** (`start_investigation`, `append_investigation_step`, `complete_investigation`) plus a convenience wrapper (`log_investigation`) so the harness can persist a run.
- **Retrieval** (`investigation_history`, `investigation_get`) so users and the LLM can reference past investigations.
- **No new domain tools for the agent loop** — every `finance_`*, `memory_*`, `goal_*`, `get_insight_history`, `meals_*`, `training_*` tool already in place is exactly what the agent loop picks from. Slice 9 only adds investigation lifecycle/history tools for audit and retrieval.

## 4) Example Surfaces (Harness-side)


| Surface                           | Why agentic                                                                                          | Indicative trajectory                                                                                           |
| --------------------------------- | ---------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `minx_investigate(question)`      | Causal/exploratory questions. LLM decides whether to drill into merchants, categories, meals, goals. | `finance_categories` → `finance_transactions(...)` → maybe `meals_list` → maybe `get_insight_history` → compose |
| `minx_plan(objective)`            | Scheduling/planning across domains. Depends on what it finds.                                        | `goal_list` → `get_goal_trajectory` → `training_list` → `meals_list` → draft → revise                           |
| `minx_retro(period, subject)`     | Causal analysis across months. LLM picks which detectors to replay, which transactions to sample.    | `get_insight_history` → `goal_trajectory` → sampling tools → synthesize                                         |
| `minx_onboard_entity(kind, name)` | Hydrates an entity/pattern page from scratch. Branches on what it finds.                             | `finance_transactions(merchant=...)` → `memory_list(subject=...)` → maybe `persist_note`                        |


Common shape: **one question in, one report out, unpredictable middle.**

## 5) Schema (Core)

Migration filename: use the next available sequential migration when this slice lands. As of 2026-04-27, Slice 6i-6l are expected to ship before investigations, so this spec no longer pre-claims `021_investigations.sql`.

```sql
CREATE TABLE investigations (
    id INTEGER PRIMARY KEY,
    harness TEXT NOT NULL,
    kind TEXT NOT NULL              -- 'investigate' | 'plan' | 'retro' | 'onboard' | 'other'
        CHECK (kind IN ('investigate', 'plan', 'retro', 'onboard', 'other')),
    question TEXT NOT NULL,
    context_json TEXT,              -- structured inputs (date range, subject, etc.)
    status TEXT NOT NULL            -- 'running' | 'succeeded' | 'failed' | 'cancelled' | 'budget_exhausted'
        CHECK (status IN ('running', 'succeeded', 'failed', 'cancelled', 'budget_exhausted')),
    answer_md TEXT,                 -- optional harness-authored rendered answer (markdown)
    trajectory_json TEXT,           -- [{step, event_template, event_slots, tool, args_digest, result_digest, latency_ms}, ...]
    response_template TEXT,         -- latest lifecycle render event, e.g. investigation.completed
    response_slots_json TEXT,       -- JSON slots for latest lifecycle render event
    citation_refs_json TEXT,        -- references used by the harness answer
    tool_call_count INTEGER,
    token_input INTEGER,
    token_output INTEGER,
    cost_usd REAL,                  -- NULL if the harness doesn't report cost
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT
);

CREATE INDEX idx_investigations_kind_started ON investigations(kind, started_at DESC);
CREATE INDEX idx_investigations_running ON investigations(status) WHERE status = 'running';
```

**Trajectory storage policy:** `trajectory_json` stores a **digest** per step (tool name, arg hash, result row count / bytes, latency). It does NOT store full tool outputs — those can be large and contain PII. Full outputs are reconstructable by replaying the tools against the DB at investigation time.

**Render storage policy:** `response_template` and `response_slots_json` store the latest lifecycle event so read APIs can expose a stable render surface without parsing trajectory text. Step-level render events are stored inside `trajectory_json` step entries.

## 6) Core MCP Tools

Lifecycle responses follow the render-contract amendment in `2026-04-28-slice9-investigation-render-contract.md`: tools return the ids below plus `response_template` / `response_slots` for lifecycle transitions. The minimal shapes shown here are the base data fields, not the complete MCP response contract.

```
start_investigation(kind, question, context_json, harness)
    -> {"investigation_id": int, "response_template": "investigation.started", "response_slots": {...}}
append_investigation_step(investigation_id, step_json)
    -> {"ok": true, "response_template": "investigation.step_logged|investigation.needs_confirmation", "response_slots": {...}}
complete_investigation(
    investigation_id,
    status,              # 'succeeded' | 'failed' | 'cancelled' | 'budget_exhausted'
    answer_md,
    tool_call_count,
    token_input,
    token_output,
    cost_usd,
    error_message,
) -> {"investigation_id": int, "response_template": "investigation.completed|investigation.failed|investigation.cancelled|investigation.budget_exhausted", "response_slots": {...}}
log_investigation(...)  # convenience wrapper with the same logging role as log_playbook_run;
                        # MCP return shape follows the render-contract amendment

investigation_history(kind=None, since=None, days=30, limit=100) -> {"runs": [...], "truncated": bool}
investigation_get(investigation_id) -> {"run": {...}}  # includes trajectory and latest response_template/response_slots
```

Mirrors the two-phase + convenience pattern from Slice 8 so the audit story is consistent.

## 7) Harness-side Loop (reference pattern)

Not implemented in this repo — documented here so the Core API is the right shape for it.

```
inv_id = start_investigation(kind, question, context_json, harness='hermes')
try:
    budget = Budget(max_tool_calls=30, max_tokens=80_000, wall_clock_s=120)
    trajectory = []
    while not done and budget.remaining():
        tool, args = llm.pick_next(question, trajectory, available_tools)
        result = mcp.call(tool, args)
        step = digest_step(tool, args, result)
        trajectory.append(step)
        append_investigation_step(inv_id, step)
        done = llm.think_done(question, trajectory)
    answer = llm.compose(question, trajectory)
    complete_investigation(inv_id, 'succeeded', answer_md=answer, ...cost...)
except BudgetExhausted:
    complete_investigation(inv_id, 'budget_exhausted', answer_md=partial, ...)
except Exception as exc:
    complete_investigation(inv_id, 'failed', error_message=str(exc), ...)
```

**Budget enforcement is mandatory.** No investigation ships without per-run caps.

## 8) Non-Goals

- No agent loops inside Core. Core exposes tools; it never calls an LLM to pick the next tool.
- No shared-memory/multi-agent orchestration. Each investigation is a single LLM driving a single trajectory.
- No "investigation chains" at the spec level. If you want "run investigation A then B", write a playbook that calls both.
- No auto-triggering of investigations from detectors (Slice 9). That's Slice 10+.

## 9) Implementation Phases

Split so Core can ship independently and the harness can build against a stable surface.


| Phase | What                                                                                                                                                                      | Where  | Effort   | Dependencies                  |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | -------- | ----------------------------- |
| 9a    | `investigations` table (next available migration) + `start_/append_/complete_/log_investigation` + `investigation_history` + `investigation_get` + tests                  | Core   | 1.5 days | Slice 6i-6l + Slice 8a merged |
| 9b    | Trajectory-digest helpers (tool name + arg hash + result digest) + PII-redaction pass for `context_json`/`answer_md` + operator runbook                                   | Core   | 1 day    | 9a                            |
| 9c    | Investigation MCP resource surface: `investigation://recent`, `investigation://{id}` (read-only) for harness UIs that want to render history without hitting the tool API | Core   | 0.5 day  | 9a                            |
| 9d    | Reference harness loop (Hermes) for `minx_investigate` — budget wrapper, LLM tool-picker, digest-and-log loop                                                             | Hermes | 3-4 days | 9a, 9b                        |
| 9e    | `minx_plan` surface (second agentic entry point reusing 9d infra)                                                                                                         | Hermes | 2 days   | 9d                            |
| 9f    | `minx_retro` + `minx_onboard_entity` surfaces                                                                                                                             | Hermes | 2-3 days | 9d                            |
| 9g    | Investigation re-query: `memory_list` gains an optional `include_cited_investigations` flag so answers can reference prior investigations                                 | Core   | 1 day    | 9a, Slice 6                   |


**Core effort: ~4 days (9a + 9b + 9c + 9g).**
**Hermes effort: ~7-9 days (9d + 9e + 9f).**

Ship order: 9a → 9b → 9d (first usable surface) → 9c + 9e + 9f + 9g in any order.

## 10) Testing Strategy

### Core tests (9a–9c)

- Two-phase lifecycle (`start` → `append` × N → `complete`) covering succeeded / failed / cancelled / budget_exhausted.
- Concurrent starts with different kinds don't collide; same-kind concurrent is allowed (investigations are user-initiated, no cron contention).
- `append_investigation_step` rejects steps after terminal status.
- Trajectory digest: `result_digest` never contains raw tool output bytes; `context_json` goes through a redaction pass for known PII fields (email, phone, account numbers).
- `investigation_history` pagination/filter matches `playbook_history` semantics.

### Harness tests (9d–9f, outside this repo)

- Budget caps respected (max_tool_calls, max_tokens, wall_clock).
- Answer is always produced for `budget_exhausted` (partial answer, never a hard crash).
- LLM tool-picker respects an allowlist (can't invoke destructive tools like `memory_reject` without explicit surface-level opt-in).

## 11) Relationship to Other Slices

- **Slice 6 (Memory):** investigations can cite memories by id in `answer_md`; `memory_get`, `memory_list`, FTS5 search, memory graph edges, and embeddings/hybrid retrieval are primary inputs.
- **Slice 8 (Playbooks):** audit pattern (two-phase + convenience wrapper) is lifted directly. A sibling to `playbook_reconcile_crashed` can be added later if crashed-running investigations need automated reconciliation; specify that tool explicitly before shipping it.
- **Slice 5 (Harness Adaptation):** a second harness would reimplement the loop against the same Core API. The agent-loop pattern is harness-specific; the tool surface is portable.