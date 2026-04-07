# Slice 2 Goals + Drift Design

**Date:** 2026-04-07
**Status:** Drafted for review
**Parent:** [Minx Life OS Roadmap — Implementation Slices](2026-04-06-minx-roadmap-slices.md)

## Goal

Define Slice 2 for Minx Core as a local-first goals and drift layer that turns Slice 1's event/review foundation into a system that can track what the user cares about, measure progress against those goals, and reflect that progress in the daily review.

This slice should also establish the correct boundary for future conversational goal capture: Minx Core owns goal facts, progress calculation, and drift detection; Hermes/Discord should be a thin client over Core goal tools rather than a second source of business logic.

## Product Outcome

After Slice 2:

- the user can create and manage goals through `minx-core`
- daily review can describe how the user is doing against active goals
- Minx can detect when current behavior is drifting away from those goals
- LLM enrichment can use active goal context when configured
- a future Hermes/Discord goal flow can be added without redesigning Core

## Scope

Slice 2 includes:

- a `goals` table in Minx Core
- Core models and service/repository logic for goal CRUD
- `minx-core` MCP tools for goal create/list/get/update/archive
- a computed `GoalProgress` read model
- `detect_goal_drift`
- `detect_category_drift`
- daily review updates to include goal-aware context
- optional OpenAI-compatible LLM enrichment via runtime config
- minimal future-proofing for a later Hermes/Discord connection

## Non-Goals

Slice 2 does not include:

- multi-user, auth, or remote-first architecture changes
- a full natural-language goal parser inside Minx Core
- a general analytics framework for arbitrary domains
- a polished Hermes/Discord conversational experience in the same milestone
- autonomy/playbooks, dashboards, or memory promotion
- broad category drift across every category in the system without goal relevance

## Architectural Decisions

### 1. Local-first remains the operating model

Slice 2 stays consistent with the current Minx posture:

- local SQLite plus vault durability
- single-user assumptions
- no auth or tenant model

This is intentional. The product risk right now is not access control complexity; it is proving that goals, progress, and review interpretation feel useful in daily use.

### 2. Minx Core owns goals

Goals are Core-level facts because they are:

- cross-domain in intent
- used by review generation and detectors
- interpretation-oriented rather than domain source-of-truth records

Domains still own factual records such as transactions, meals, or workouts. Goals refer to those records through structured filters and read models.

### 3. Hermes/Discord is a thin layer on top of Core tools

Hermes/Discord integration should not arrive before Core goal tools exist and stabilize.

The correct sequence is:

1. build Core goal tools and goal-aware review behavior
2. validate the structured API and stored goal model
3. add Hermes/Discord goal capture as a thin client that gathers language from the user and calls the Core tools

This keeps business logic in one place and avoids burying validation rules in prompts or chat flows.

### 4. LLM enrichment is optional

Goals and drift detection must work without any LLM configured.

If LLM enrichment is enabled, it should improve:

- narrative quality
- prioritization language
- next-day focus synthesis

It must not be required for:

- goal CRUD
- progress calculation
- detector output
- basic daily review generation

## Data Model

Slice 2 adds a `goals` table in Minx Core with the following fields:

- `id`
- `goal_type`
- `title`
- `status`
- `metric_type`
- `target_value`
- `period`
- `domain`
- `filters_json`
- `starts_on`
- `ends_on`
- `notes`
- `created_at`
- `updated_at`

### Field semantics

- `goal_type`: a coarse classification such as `spending_cap`, `spending_reduction`, or future cross-domain types
- `status`: `active`, `paused`, `archived`, or `completed`
- `metric_type`: initial supported values are `sum_below`, `sum_above`, `count_below`, `count_above`
- `target_value`: stored in the natural unit for the metric; finance amounts use cents
- `period`: initial supported values should be bounded and explicit, such as `daily`, `weekly`, `monthly`, `rolling_28d`
- `domain`: first expected value is `finance`, but the shape should remain domain-capable
- `filters_json`: structured selector payload used to scope the goal, such as category, merchant, account, or future domain constraints

### Why `filters_json`

The first Slice 2 goal system should be finance-first without creating many narrow columns that will age badly. A structured filter payload lets Core express:

- "Dining out under $250/month"
- "Amazon below last month's level"
- "Discretionary spend count below N transactions/week"

without prematurely inventing a large typed schema for every future domain.

## Core MCP Tool Surface

Slice 2 adds these tools to `minx-core`:

- `goal_create`
- `goal_list`
- `goal_get`
- `goal_update`
- `goal_archive`

### Tool responsibilities

- `goal_create`: validate and store a new goal
- `goal_list`: return active or filtered goals
- `goal_get`: return one goal with current derived progress
- `goal_update`: edit allowed goal fields
- `goal_archive`: move a goal out of active use without deleting history

### Explicit omission

Slice 2 does not add a natural-language parsing tool in Core. If a harness wants conversational creation, it should translate user language into a structured `goal_create` call.

## Read Models

Slice 2 adds a computed `GoalProgress` read model.

For the initial version, `GoalProgress` should be built on demand from existing source-of-truth finance data plus goal filters. It should not introduce a new heavy persistence layer.

`GoalProgress` should include:

- goal identity and metadata
- current period/window
- current actual value
- target value
- remaining budget or gap-to-target where meaningful
- simple status classification such as `on_track`, `watch`, `off_track`, or `met`
- supporting numbers suitable for detector and review use

## Detector Design

### `detect_goal_drift`

This detector should fire when an active goal is materially off trajectory for the current period.

Examples:

- current monthly dining-out spend is too high for the elapsed point in the month
- discretionary purchase count is rising faster than the target permits

The first implementation should be conservative. Small fluctuations should not produce noise.

### `detect_category_drift`

This detector should compare recent behavior to a recent baseline for categories relevant to active goals.

For Slice 2, category drift should stay scoped to goal-relevant categories or filters rather than scanning the whole finance space. That keeps the signal product-focused and avoids alert spam.

## Daily Review Changes

Daily review should become goal-aware in three places:

1. **Structured artifact**
  Add a compact goal status section or equivalent structured field containing active-goal progress summaries.
2. **Fallback narrative**
  When no LLM is configured, the deterministic narrative should still mention major goal pressure or goal wins in plain language.
3. **LLM prompt context**
  When LLM enrichment is enabled, the prompt should include only:
  - active goals
  - current progress values
  - recent relevant drift signals

It should not dump full goal history or unrelated records into the prompt.

## LLM Enrichment Configuration

The first supported provider path should be an OpenAI-compatible HTTP configuration loaded from `core/llm_config`.

Expected config shape:

- `provider: "openai_compatible"`
- `base_url`
- `model`
- `api_key_env`
- optional request settings such as timeout or temperature if needed

### Configuration timing

This provider should be wired at the beginning of Slice 2, before heavy goal-aware prompt work.

Reason:

- Slice 2 makes the enriched review path more important
- the current detector-only fallback path is already proven
- goal-aware prompt behavior should be exercised with a real provider before prompt complexity grows further

### Secret handling

API keys must not be stored in source or preference payloads. The config should reference an environment variable name such as `OPENAI_API_KEY`.

## Hermes/Discord Connection Timing

Hermes/Discord integration should happen after the Core goal tools are stable, not before.

The threshold for adding that connection is:

- `goal_create`, `goal_update`, `goal_list`, and `goal_archive` exist
- the goal schema has stabilized enough to avoid constant churn
- daily review can already reason about goals without the harness
- at least one OpenAI-compatible provider path is wired for optional enrichment
- manual or automated end-to-end tests show harness-friendly Core tool behavior

In practice, this means Hermes/Discord goal capture should be the second half of Slice 2 or a narrow Slice 2.1 immediately after the Core foundation lands.

## Durability and Persistence Rules

Slice 2 should respect the existing durability truth:

- SQLite remains the source of truth for goals and persisted insights
- vault remains a projection sink for human-readable review output
- global atomicity across SQLite and vault is still not guaranteed

New Slice 2 persisted state should not make this worse by accident.

Specifically:

- goals must persist in SQLite only
- goal progress should remain computed on demand for the first milestone
- if review artifacts incorporate goal-aware insights, they should follow the same partial-failure semantics already used by `generate_daily_review`

## Event and Sensitivity Rules

Slice 2 should treat `sensitivity` as a policy problem, not a schema-addition problem. The `events` table already contains a `sensitivity` column from Slice 1.

What Slice 2 should add is:

- explicit use of sensitivity-aware filtering where needed
- redaction policy decisions for goal-aware review inputs and outputs

Slice 2 should also ensure any new event types or future goal-related events are registered explicitly in the event payload model map so they cannot silently drift.

## Implementation Sequence

### Phase 0: Stabilization front-load

- make `daily_review` async-safe or loop-safe
- wire one OpenAI-compatible provider
- add a minimal integration test for the enriched review path

### Phase 1: Goals foundation

- add migration for `goals`
- add goal models and validation
- add repository/service layer
- add `minx-core` goal CRUD tools

### Phase 2: Goal-aware review

- add `GoalProgress`
- add goal-aware fallback narrative
- extend review artifact and prompt context

### Phase 3: Drift detection

- implement `detect_goal_drift`
- implement goal-scoped `detect_category_drift`
- integrate detector results into review output

### Phase 4: Thin harness connection

- add Hermes/Discord flow that gathers goal intent conversationally
- translate that flow into structured Core goal tool calls
- keep conversational policy out of Core

## Testing Strategy

Slice 2 should extend the current test posture rather than invent a new one.

Required coverage:

- migration tests for the `goals` table
- unit tests for goal validation and CRUD behavior
- read model tests for `GoalProgress`
- detector tests for `detect_goal_drift` and `detect_category_drift`
- review tests showing goal-aware fallback output
- one integration test covering the configured OpenAI-compatible LLM path
- Core MCP tool tests for the new goal tools

## Success Criteria

Slice 2 is successful when:

- goals can be created and managed through `minx-core`
- active goals appear in review reasoning
- drift is detected without excessive alert noise
- the review still works cleanly with no LLM configured
- LLM enrichment works when `core/llm_config` is present and valid
- Hermes/Discord can be added as a thin client layer rather than a Core rewrite