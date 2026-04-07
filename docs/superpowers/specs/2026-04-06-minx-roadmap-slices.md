# Minx Life OS Roadmap — Implementation Slices

**Date:** 2026-04-06
**Status:** Active
**Parent:** [Minx Life OS Architecture Design](2026-04-06-minx-life-os-architecture-design.md)

Each slice gets its own spec, plan, and implementation cycle. Slices are ordered by dependency and value delivery.

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

**Delivers:** First end-to-end cross-domain value. Any harness triggers a review via the Core MCP `daily_review` tool and renders a structured artifact to Discord + vault.

**Dependencies:** None (builds on existing Finance MCP + shared platform)

**Implementation notes:**
- Insight dedup uses `(review_date, insight_type, dedupe_key)` instead of the spec's `(review_date, insight_type, summary)`. `dedupe_key` is an explicit field on `InsightCandidate`, giving callers control over identity rather than relying on summary text.
- Finance read API exposes `get_import_job_issues()` (covering both failed and stale jobs) instead of the spec's `get_failed_imports()`.
- The `insights` table omits `event_count` from the spec; dedup is handled entirely through `dedupe_key`.
- `ReviewDurabilityError` provides partial-failure semantics: if the in-memory review is built but a durability sink (detector DB write or vault note) fails, the error carries the artifact on `exc.artifact` and per-sink details on `exc.failures`.

---

## Slice 2: Goals + Deeper Detection

**Status:** Not started

**Scope:**
- Goals table and CRUD in Minx Core
- Goal-setting through Hermes/Discord (conversational creation)
- GoalProgress read model
- `detect_goal_drift` detector: trajectory vs spending/activity targets
- `detect_category_drift` detector: 4-week rolling average comparison
- Event `sensitivity` column + redaction policy for sensitive events
- Insight expiration: filter-on-read (`WHERE expires_at IS NULL OR expires_at > now()`)
- Read model snapshots stored alongside insights for reproducibility
- Richer LLM evaluation with goal context

**Delivers:** Minx tracks what the user cares about and measures against it. Detection gets smarter with data history.

**Dependencies:** Slice 1 (event contract, review pipeline, detectors)

---

## Slice 3: Meals MCP

**Status:** Not started

**Scope:**
- Meals domain MCP server: meal logs, foods/ingredients, recipes, nutrition facts
- Service layer, SQLite schema, MCP tools following Finance pattern
- Event emission: `meal.logged`, `nutrition.day_updated`, `meal.plan_updated`
- Reuses shared platform: contracts, jobs, vault writer, event infrastructure
- NutritionSnapshot read model in Minx Core
- Meals-specific detectors: nutrition gaps, protein tracking, meal frequency
- Cross-domain detectors: restaurant spend vs meal prep days
- DailyReview updated to include nutrition section

**Delivers:** Second domain live. Cross-domain insights become real — correlating spending with eating habits.

**Dependencies:** Slice 1 (event contract, shared platform patterns)

---

## Slice 4: Training MCP

**Status:** Not started

**Scope:**
- Training domain MCP server: workout plans, exercise library, session logs, progression
- Service layer, SQLite schema, MCP tools following Finance/Meals pattern
- Event emission: `workout.completed`, `training.program_updated`, `training.milestone_reached`
- TrainingSnapshot read model in Minx Core
- Training-specific detectors: adherence trends, volume progression, recovery signals
- Cross-domain detectors: training + nutrition correlation, training + spending patterns
- DailyReview updated to include training section

**Delivers:** Third domain. Finance, nutrition, and training all in one daily review.

**Dependencies:** Slice 1 (event contract), Slice 3 (cross-domain patterns established)

---

## Slice 5: Harness Adaptation + Poll Adapter

**Status:** Not started

**Scope:**
- Harness registry: harnesses identify themselves, Core selects behavior profile
- Behavior profiles: context budget, response length, retrieval depth, autonomy level, output format
- Hermes profile: concise, conversational, proactive, summary-heavy
- Claude Code profile: deeper retrieval, structured output, tool-forward
- Profile auto-detection with manual override
- Poll adapter infrastructure: base class, interval, idempotency, state tracking, error handling
- Vault poll adapter: surfaces journal/ideas content into Minx Core without a dedicated MCP
- Poll adapter integrated into review pipeline as secondary data source

**Delivers:** Minx behaves appropriately per context. Vault content flows into reviews without needing a Journal MCP yet.

**Dependencies:** Slice 1 (review pipeline), at least one additional domain (slice 3 or 4) to make adaptation meaningful

---

## Slice 6: Memory Promotion + Durable Memory

**Status:** Not started

**Scope:**
- Structured durable memory system: preferences, recurring patterns, stable constraints
- Memory store (separate from insights): facts Minx has learned about the user
- Auto-promotion policy: low-risk patterns promoted automatically (recurring meals, recurring merchants, soft preferences)
- Confirmation gate: identity-level or commitment-level memories require user approval
- Recurring pattern detection across domains
- Memory retrieval integrated into review pipeline (personalized insights)
- Memory used for context: "you usually spend less in April", "you prefer morning workouts"

**Delivers:** Minx remembers and uses what it learns. Reviews gain personal context over time.

**Dependencies:** Slice 1 (review pipeline, insight records), multiple domains active (slices 3-4) for cross-domain patterns

---

## Slice 7: Ideas/Journal MCP

**Status:** Not started

**Scope:**
- Journal domain MCP server: entries, reflections, captured ideas, linked references
- Service layer, SQLite schema, MCP tools following established pattern
- Event emission: `idea.captured`, `journal.entry_added`, `journal.reflection_added`
- JournalSnapshot read model in Minx Core
- Journal recap section in DailyReview
- Open loop detection for unfinished thoughts/intentions
- Cross-domain detectors: journal mood + spending/training correlation
- Replaces vault poll adapter for journal content with structured source of truth

**Delivers:** Fourth domain. Daily review covers spending, nutrition, training, and reflections.

**Dependencies:** Slice 1 (event contract), Slice 5 (vault poll adapter may be replaced)

---

## Slice 8: Proactive Autonomy

**Status:** Not started

**Scope:**
- Playbook infrastructure: each playbook has trigger, bounded action, success metric, kill switch, owner
- First playbooks:
  - Daily review auto-trigger (scheduled, no user action needed)
  - Weekly summary auto-generate
- Guardrailed automation:
  - Auto-categorize high-confidence transactions (>95% match on existing rules)
  - Meal plan reminders based on grocery patterns
  - Training session reminders based on program schedule
- Escalation policy: when to act vs when to ask
- Audit trail for all autonomous actions
- User-facing controls: enable/disable per playbook, configure triggers

**Delivers:** Minx starts doing things without being asked — carefully, with guardrails and kill switches.

**Dependencies:** Slice 1 (review pipeline), Slice 2 (goals for target-aware automation), scheduling infrastructure

---

## Slice 9: Dashboard + Richer Surfaces

**Status:** Not started

**Scope:**
- Web dashboard: HTTP app serving review data, goal progress, domain summaries
- Dashboard cards backed by read models (reuses existing read model infrastructure)
- Historical review browsing (query past DailyReview artifacts)
- Multi-surface rendering: Discord digest, vault note, dashboard cards, future clients all from the same DailyReview artifact
- Goal progress visualization
- Spending and nutrition trend charts
- Training adherence calendar view

**Delivers:** Visual interface into the Life OS state. The same data that drives Discord digests now drives a dashboard.

**Dependencies:** Slice 1 (review pipeline, read models), Slice 2 (goals), ideally slices 3-4 (multiple domains for richer dashboard)

---

## Dependency Graph

```
Slice 1 (Events + Review)
    |
    +---> Slice 2 (Goals + Detection)
    |         |
    +---> Slice 3 (Meals MCP)
    |         |
    |         +---> Slice 4 (Training MCP)
    |         |         |
    |         +---------+---> Slice 6 (Memory)
    |                             |
    +---> Slice 5 (Harness + Poll)  |
    |         |                     |
    |         +---> Slice 7 (Journal MCP)
    |
    +---> Slice 8 (Autonomy) <--- Slice 2
    |
    +---> Slice 9 (Dashboard) <--- Slices 2, 3, 4
```

Slice 1 is the foundation for everything. Slices 2-4 can be parallelized after slice 1. Slices 5-7 depend on having multiple domains. Slices 8-9 are the capstone layers.

## Principles Across All Slices

- Each slice gets its own spec, plan, and implementation cycle
- Domains own facts, Minx Core owns interpretation
- Event-driven integration: domains emit, Core consumes
- Harnesses own conversation style and rendering
- New domains follow the Finance pattern (service layer, SQLite, MCP tools, event emission)
- Anti-bloat: a feature only ships if it improves capture, review, planning, or goal alignment
- Every autonomous behavior has a trigger, bounded action, success metric, and kill switch
