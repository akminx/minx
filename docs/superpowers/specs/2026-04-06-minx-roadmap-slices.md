# Minx Life OS Roadmap — Implementation Slices

**Date:** 2026-04-06
**Status:** Active (updated 2026-04-14 after Slice 4 + Hermes cutover)
**Parent:** [Minx Life OS Architecture Design](2026-04-06-minx-life-os-architecture-design.md)

Each slice gets its own spec, plan, and implementation cycle. The roadmap is ordered by dependency and by trust: Minx should first become useful, then cross-domain, then harness-aware, then durable, and only after that more autonomous.

## Current Projection

The roadmap now follows four arcs:

- **Foundation:** establish portable Core state, structured review, goals, and safe review boundaries
- **Domain Expansion:** add concrete life domains so Minx has a real cross-domain picture
- **Interaction + Trust:** add harness-specific flows, sensitivity policy, and ambient inputs without moving business logic out of Core
- **Intelligence + Autonomy:** add durable memory, reproducibility, bounded playbooks, and richer surfaces

From the current repo baseline, the next recommended execution order is:

1. Slice 6: Durable Memory + Review Reproducibility
2. Slice 7: Ideas/Journal MCP
3. Slice 8: Proactive Autonomy
4. Slice 9: Dashboard + Richer Surfaces

Slice 5 (Harness Adaptation) has been removed. The MCP protocol itself provides portability. Harness-specific behavior lives in the harness (e.g. Hermes skill files), not in Core. See the MCP / Harness Responsibility Split section in the architecture design.

This ordering preserves the architecture doc's north star:

- Minx Core stays the owner of interpretation
- domains stay the owner of facts
- harness-specific instance setup stays outside Core and arrives later than the reusable Core contracts
- autonomy waits until memory, trust, and review durability are mature enough

---

## Slice 1: Event Pipeline + Daily Review

**Status:** Implemented
**Spec:** [2026-04-06-slice1-event-pipeline-daily-review-design.md](2026-04-06-slice1-event-pipeline-daily-review-design.md)

**Scope:**
- Event contract: `events` table, `emit_event()` function, payload schemas (Pydantic)
- Event versioning via `schema_version` column
- Finance MCP wired to emit events on import, categorize, report, anomaly scan
- Finance read API: typed interface Core calls instead of raw table access
- Minx Core package: `minx_mcp/core/`
- Read models: DailyTimeline, SpendingSnapshot, OpenLoopsSnapshot (computed on-demand)
- 2 detectors: spending spike, open loops
- Review pipeline: detectors -> LLM evaluation pass -> DailyReview artifact
- Template fallback when LLM unavailable
- Vault note output: `Minx/Reviews/YYYY-MM-DD-daily-review.md`
- Timezone preference for date filtering
- Review idempotency (insight dedup + vault overwrite)
- Quiet day handling
- Core MCP server with `daily_review` tool (harness entry point)

**Delivers:** First end-to-end value. Any harness can trigger a review through Core and receive a structured artifact plus a vault-facing markdown projection.

**Dependencies:** None

**Implementation notes:**
- Insight dedup uses `(review_date, insight_type, dedupe_key)` instead of summary text.
- Finance read API exposes `get_import_job_issues()` instead of the earlier `get_failed_imports()` wording.
- `ReviewDurabilityError` carries the built artifact plus per-sink failures when SQLite/vault durability only partially succeeds.

---

## Slice 2: Goals + Deeper Detection

**Status:** Implemented for the repo-contained Core scope
**Spec:** [2026-04-07-slice2-goals-drift-design.md](2026-04-07-slice2-goals-drift-design.md)

**Scope:**
- Goals table and CRUD in Minx Core
- Harness-friendly Core goal tool surface for later conversational clients
- GoalProgress read model
- `detect_goal_drift` detector: trajectory vs spending/activity targets
- `detect_category_drift` detector: recent equal-length baseline comparison for goal-relevant filters
- Goal-aware daily review artifact and fallback narrative
- Optional OpenAI-compatible LLM enrichment
- Review-path handling for non-`normal` events

**Delivers:** Minx can track what the user cares about, compute current progress, and reflect that in the review without depending on a specific harness.

**Dependencies:** Slice 1

**Implementation notes:**
- `goal_get` returns both the stored goal DTO and derived progress, with `progress: null` outside the goal lifetime.
- `goal_list()` defaults to active goals; `goal_list(status=...)` is the explicit path for other lifecycle states.
- Goal progress uses the natural period window intersected with the goal lifetime.
- `detect_category_drift` compares the current elapsed span against the immediately preceding equal-length baseline span and works for category-, merchant-, and account-scoped goals.
- `daily_review` now returns a protected projection at the MCP boundary rather than the raw internal review artifact.

**Still deferred after Slice 2** (status updated after Slice 2.5):
- ~~transport-agnostic conversational capture~~ → Delivered in Slice 2.1 (`goal_capture`) and refined in Slice 2.5 (`goal_parse`)
- ~~protected review boundary hardening~~ → Replaced by the MCP/harness split in Slice 2.5. Core returns structured snapshots; the harness owns narrative.
- insight expiration filtering → Deferred to Slice 6 (Durable Memory)
- read-model snapshot persistence for reproducibility → Deferred to Slice 6

---

## Slice 2.1: Conversational Goals + Trust Hardening

**Status:** Implemented for the repo-scoped Core/harness-agnostic work; external harness setup deferred

**Scope:**
- transport-agnostic conversational goal capture in Core
- prompt/policy layer that translates natural language into structured `goal_create` and `goal_update` calls
- stronger sensitivity policy for review inputs and outputs
- redaction rules for sensitive events and goal-related artifacts
- review/client contract checks so harnesses consume the structured review artifact deliberately
- end-to-end goal flow verification from conversational input -> Core tools -> protected review output

**Delivers:** A reusable Core goal-capture surface plus a protected review boundary that any MCP-capable harness can consume without embedding business logic in the client.

**Dependencies:** Slice 2

**Implementation notes:**
- `goal_capture` now exists in Core as a deterministic `create` / `update` / `clarify` / `no_match` proposal tool.
- `daily_review` now returns a protected client-facing projection with explicit redaction metadata by default.
- Unit, server, stdio, and repo-level e2e tests cover the Core-side conversational-goal and protected-review flows.

**Still deferred after repo-scoped Slice 2.1:**
- Hermes/Discord or other harness-specific instance setup
- session state, UI flow, and client orchestration outside Core
- broader trust policy evolution beyond the default protected boundary

**Why this exists as a separate slice:**
- It keeps business logic in Core and lets harness-specific shells stay thin.
- It gives trust/privacy work a home instead of pretending it is free follow-up polish.
- It prevents us from moving into more public or proactive surfaces before the review boundary is safe enough.

---

## Slice 2.5: MCP Surface Refactor

**Status:** Implemented
**Spec:** [2026-04-10-slice2.5-mcp-surface-refactor-design.md](2026-04-10-slice2.5-mcp-surface-refactor-design.md)

**Scope:**
- Replace `daily_review` with `get_daily_snapshot` (structured data, silent signal persistence, no narrative)
- Add `get_insight_history` tool (temporal signal queries with recurrence counts)
- Add `get_goal_trajectory` tool (goal progress across historical periods with trend)
- Add `persist_note` tool (generic vault write for harness-generated content)
- Refactor `goal_capture` → `goal_parse` (parse-only, dual-path structured/natural input)
- Add dual-path inputs to `finance_query` (structured filters OR natural language)
- Remove narrative generation, markdown rendering, vault writing, and LLM review evaluation from Core
- Update architecture doc and roadmap to reflect MCP/harness split

**Delivers:** A Core MCP tool surface designed for smart harness consumption. Hermes (or any future harness) gets structured data, historical signals, and goal trajectories instead of pre-rendered narratives. The MCP becomes a portable data+signals provider that works with any MCP-capable harness.

**Dependencies:** Slices 1, 2, 2.1

**What stays unchanged:** All Finance MCP tools, all detectors, all read model builders, all domain logic, all shared platform code, all schema migrations.

**Implementation notes:**
- `goal_capture.py` fully absorbed into `goal_parse.py` (single module, no cross-file private imports).
- `LLMInterface` and `LLMReviewResult` remain in `models.py` — they are live dependencies of the interpretation pipeline (`llm.py`, `llm_openai.py`), not dead code. The spec's removal directive was incorrect; these types serve the finance query and goal capture LLM paths, not the old review pipeline.
- `_compute_trend` uses least-squares slope instead of first-vs-last comparison.
- 392 tests passing, mypy clean on 60 source files.

---

## Slice 3: Meals MCP

**Status:** Implemented

**Scope:**
- Meals domain MCP server: meal logs, foods/ingredients, recipes, nutrition facts
- Service layer, SQLite schema, MCP tools following the Finance pattern
- Event emission: `meal.logged`, `nutrition.day_updated`, `meal.plan_updated`
- NutritionSnapshot read model in Minx Core
- Meals-specific detectors: nutrition gaps, protein tracking, meal frequency
- Cross-domain detectors: restaurant spend vs meal prep days
- DailySnapshot and Core read models updated to include NutritionSnapshot

**Delivers:** The first meaningful second domain. Cross-domain insight becomes real instead of aspirational.

**Dependencies:** Slice 1

---

## Slice 4: Training MCP

**Status:** Implemented

**Scope:**
- Training domain MCP server: workout plans, exercise library, session logs, progression
- Service layer, SQLite schema, MCP tools following the Finance/Meals pattern
- Event emission: `workout.completed`, `training.program_updated`, `training.milestone_reached`
- TrainingSnapshot read model in Minx Core
- Training-specific detectors: adherence trends, volume progression, recovery signals
- Cross-domain detectors: training + nutrition correlation, training + spending patterns
- DailySnapshot and Core read models updated to include TrainingSnapshot

**Delivers:** A third domain that makes Minx feel like a real Life OS rather than a finance review with extras.

**Dependencies:** Slice 1, ideally after Slice 3

**Architecture patterns to adopt in this slice:**

- **Timeline normalization (informed by ErikBjare/quantifiedme):** With three domains emitting events, the Core timeline should normalize entries into `(start, end, data)` tuples for cross-domain merging. Each domain emits events in its own shape; Core normalizes them into a unified timeline view. This is the natural point to formalize timeline composition since two domains (Finance, Meals) won't yet pressure the merging logic enough to prove the abstraction.
- **FastMCP middleware (informed by jlowin/fastmcp):** Add `TimingMiddleware` and `StructuredLoggingMiddleware` to all three MCP servers. With Finance + Meals + Core all running, structured tool-call logging becomes essential for debugging cross-domain flows. This requires adding the `fastmcp` package as a dependency (the middleware system is in the standalone package, not the `mcp` SDK). Evaluate whether the middleware API is stable enough to adopt at that point.

---

## Slice 5: Harness Adaptation + Ambient Inputs

**Status:** Removed

This slice was removed in the Slice 2.5 redesign. The MCP protocol itself provides portability — harnesses adapt their own behavior through their skill/plugin systems (e.g. Hermes skill files). Minx Core does not need a harness registry, behavior profiles, or per-harness configuration. See the MCP / Harness Responsibility Split in the architecture design and the Slice 2.5 spec for details.

Ambient input ingestion (vault polling, journal scanning) remains harness-level work. Hermes already handles this through its journal-scan cron skill.

---

## Slice 6: Durable Memory + Review Reproducibility

**Status:** Not started

**Scope:**
- structured durable memory store for stable preferences, recurring patterns, and constraints
- memory promotion policy with confirmation gates for high-risk memories
- insight expiration filtering so stale detector output does not accumulate forever
- read-model snapshot persistence or equivalent reproducibility mechanism for review/debug history
- retrieval path that can explain why Minx said something on a given day
- review pipeline integration for durable memory without collapsing into transcript recall

**Delivers:** Minx becomes explainable and durable. Reviews gain long-term context, and future autonomy/dashboard work has a stable foundation to build on.

**Dependencies:** Slice 1, plus meaningful domain activity from Slices 3-4 and ideally Slice 5

**Why this comes before autonomy:**
- bounded autonomy without durable memory and reproducibility is hard to trust
- dashboards and audits also need stable historical grounding

**Architecture patterns to adopt in this slice:**

- **OTel tracing for MCP servers (informed by traceloop/openllmetry):** The `opentelemetry-instrumentation-mcp` package provides drop-in OTel tracing for FastMCP tool calls. With Finance + Meals + Core all running with real traffic by this point, structured observability becomes essential for debugging cross-domain signal chains and reproducing review state. Point at local Jaeger or stdout exporter for development; keep the instrumentation boundary thin so it does not couple domain logic to the tracing library.
- **CTE-based event replay views (informed by mattbishop/sql-event-store):** For review reproducibility, add SQL views that replay events into read-model-equivalent state using CTEs. This keeps replay logic in SQL rather than scattered Python, makes it inspectable via `sqlite3` CLI, and provides the "explain why Minx said something on a given day" capability without requiring full read-model snapshot persistence. The CTE approach is particularly suited to SQLite since it avoids materialized views while remaining performant for the expected event volumes.

---

## Slice 7: Ideas/Journal MCP

**Status:** Not started

**Scope:**
- Journal domain MCP server: entries, reflections, captured ideas, linked references
- Service layer, SQLite schema, MCP tools following the established domain pattern
- Event emission: `idea.captured`, `journal.entry_added`, `journal.reflection_added`
- JournalSnapshot read model in Minx Core
- Journal recap in DailySnapshot
- Open loop detection for unfinished thoughts and intentions
- Cross-domain detectors: journal mood + spending/training correlation
- gradual replacement of ad hoc ambient vault ingestion where a structured source becomes better

**Delivers:** Reflection becomes a first-class domain instead of a side channel, which makes Minx more like a real personal OS and less like a structured tracking stack.

**Dependencies:** Slice 1, and benefits from Slice 5 for ambient-input posture

---

## Slice 8: Proactive Autonomy

**Status:** Not started

**Scope:**
- Playbook infrastructure: trigger, bounded action, success metric, kill switch, owner
- first playbooks:
  - daily review auto-trigger
  - weekly summary auto-generate
  - tightly scoped high-confidence maintenance actions
- escalation policy: when Minx acts vs when it asks
- audit trail for autonomous actions
- user-facing controls per playbook
- goal-aware and memory-aware action selection

**Delivers:** Minx starts doing bounded, trustworthy work without drifting into a vague always-on agent loop.

**Dependencies:** Slice 2, Slice 5, Slice 6, and stable scheduling infrastructure

---

## Slice 9: Dashboard + Richer Surfaces

**Status:** Not started

**Scope:**
- web dashboard serving review data, goal progress, domain summaries, and historical artifacts
- dashboard cards backed by the same read models and durable review state as other surfaces
- historical review browsing
- richer visualizations for goals, spending, nutrition, and training
- multi-surface rendering from the same underlying review artifact and memory layers

**Delivers:** Visual interface into the Life OS state. The same structured system that powers Discord and CLI can drive a richer dashboard without a parallel logic stack.

**Dependencies:** Slice 1, Slice 2, and ideally Slices 3-6

---

## Dependency Graph

```text
Slice 1 (Events + Review)
    |
    +---> Slice 2 (Goals + Detection)
    |         |
    |         +---> Slice 2.1 (Conversational Goals + Trust)
    |                    |
    |                    +---> Slice 2.5 (MCP Surface Refactor)
    |
    +---> Slice 3 (Meals MCP / SousChef port)
    |         |
    |         +---> Slice 4 (Training MCP)
    |
    +---> Slice 6 (Memory + Reproducibility)
    |         |
    |         +---> Slice 8 (Autonomy)
    |
    +---> Slice 7 (Ideas/Journal MCP)
              |
              +---> Slice 9 (Dashboard + Richer Surfaces)

Slice 5 (Harness Adaptation) removed — portability is provided by MCP protocol.
Slice 2.5 should be done before Slice 3 to establish the harness-ready tool surface.
Slice 9 also depends on Slice 2 and benefits strongly from Slices 3-6.
```

## Principles Across All Slices

- Each slice gets its own spec, plan, and implementation cycle
- Domains own facts, Minx Core owns interpretation
- Event-driven integration remains the default
- Harnesses own conversation style and rendering, not business logic
- New domains should follow the Finance pattern unless there is a strong reason not to
- Trust work is a real product surface, not invisible infrastructure
- Durable memory must stay queryable and explainable, not collapse into transcript sprawl
- Autonomy only expands after review, trust, and memory layers are credible
