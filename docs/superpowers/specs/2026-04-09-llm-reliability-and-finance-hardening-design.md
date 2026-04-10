# Minx LLM Reliability + Finance Hardening Design

**Date:** 2026-04-09
**Status:** Drafted for implementation
**Scope:** Improve conversational reliability, finance query usability, importer robustness, and internal architecture quality while preserving Minx's local-first, deterministic core
**Parent:** [2026-04-06-minx-life-os-architecture-design.md](2026-04-06-minx-life-os-architecture-design.md)

## Goal

Upgrade the current Minx repo so that conversational interpretation, importer identification, and finance querying become substantially more reliable without turning the project into an opaque LLM-driven system.

This design keeps Minx's current architectural direction:

- domains own facts
- Core owns interpretation
- transports stay thin
- deterministic code remains the source of truth for validation, persistence, contracts, and safety

The key change is that Minx will begin treating a low-cost LLM as a normal interpretation component for fuzzy-input tasks, while still forcing typed outputs, deterministic validation, and explicit mutation boundaries.

## Why This Work Matters Now

The repo is already strong in storage, contracts, migration discipline, and test coverage. The highest remaining weaknesses are not basic engineering quality. They are interpretation and UX reliability:

- `goal_capture` is regex-heavy and brittle for real language
- import source detection is filename-based and fragile
- `sensitive_finance_query` is too narrow for conversational use
- parsed import hints such as `category_hint` are left unused
- some service and interpretation modules have grown dense enough that future changes will get harder than they need to be

Because the project currently has no active users, this is the right time to improve both behavior and boundaries together rather than preserving weak internals for compatibility reasons.

## Success Criteria

This design is successful when:

- conversational goal capture works reliably on realistic user phrasing
- importer/source detection no longer depends primarily on filenames
- finance supports natural-language read queries that resolve into safe structured filters
- all LLM-driven features are schema-constrained, validated, and cheap enough for single-user everyday use
- deterministic business logic remains responsible for validation, mutation, calculations, and policy
- the codebase gets easier to extend for Meals, Training, and later harness adaptation work
- the best applicable ideas from external reference projects are incorporated without importing unnecessary complexity

## Non-Goals

This design does not attempt to:

- replace core business logic with LLM calls
- let models write SQL directly or mutate data directly
- build full multi-model tiering in this pass
- redesign the full event architecture or durable memory system in one step
- implement Slice 3 or future domains directly
- create a generalized autonomous agent loop

## Core Design Decision

Minx should use LLMs selectively for interpretation problems, not for truth-maintaining logic.

The governing rule for this design is:

- LLMs interpret
- deterministic code validates
- deterministic code executes

That means:

- LLMs may classify, resolve, infer, or translate natural language into typed plans
- deterministic code must still validate supported operations, names, dates, ranges, and invariants
- all writes remain explicit and contract-backed
- all math, storage, policy, and durability remain deterministic

## Model Strategy

This design assumes one default low-cost model for all interpretation tasks.

Why one model now:

- the project is single-user and local-first
- cost predictability matters
- a single model reduces prompt/config sprawl
- model-tiering can be added later if evidence shows the low-cost model is insufficient

The system should still be architected so model selection can become configurable later, but tiered routing is intentionally deferred.

## Where LLMs Should Be Used

### 1. Conversational Goal Capture

Current `goal_capture` is limited by regex extraction and exact phrase matching. This is the strongest immediate LLM candidate.

The model should receive:

- user message
- review date
- active goals
- supported goal actions
- allowed goal family constraints for this phase
- known finance categories
- known merchants
- exact JSON schema for the result

The model should return only a typed `GoalCaptureResult`-compatible proposal:

- `create`
- `update`
- `clarify`
- `no_match`

Deterministic code must still:

- validate the result shape
- verify referenced goals/categories/merchants exist
- enforce supported goal family constraints
- preserve the explicit `goal_capture` -> `goal_create` / `goal_update` mutation boundary

### 2. Import Source Detection

Current import detection relies on filenames. This should be replaced by interpretation over lightweight file evidence.

The model should receive:

- filename
- extension
- first 3-5 rows of CSVs or extracted summary text for PDFs
- supported source kinds
- expected columns/features for each source kind
- exact JSON schema for a detection result

The model should return a typed detection result:

- detected source kind
- confidence
- optional mapping hints
- clarify flag if uncertain

Deterministic code must still:

- verify the returned source kind is supported
- require mapping where generic CSV needs one
- fall back to clarify/error if confidence or structure is insufficient

### 3. Natural-Language Finance Query Translation

Minx should support a new finance read path that interprets natural-language requests into safe structured filters.

Examples:

- "show me everything at Whole Foods last month"
- "how much did I spend on restaurants this week"
- "show my uncategorized transactions from March"

The model should receive:

- current date
- supported intents
- allowed filter fields
- known categories
- known merchants
- known accounts
- exact JSON schema for a query plan

The model should return a typed query plan such as:

- intent: `list_transactions` | `sum_spending` | `count_transactions`
- filters: `start_date`, `end_date`, `category_name`, `merchant`, `account_name`, `description_contains`
- confidence
- clarify metadata when ambiguous

Deterministic code must still:

- validate the plan
- resolve exact matches or clarifications
- execute parameterized SQL only through known-safe query builders
- never let the LLM produce raw SQL

## Where LLMs Should Not Be Used

LLMs should not be used for:

- goal validation
- goal progress calculation
- anomaly calculations
- review redaction policy
- audit logging
- filesystem safety
- migration application
- query execution
- persistence logic
- contract wrapping

These are all stronger, cheaper, and more trustworthy as deterministic code.

## Shared Interpretation Layer

Instead of scattering ad hoc prompt code across multiple files, Minx should gain one shared interpretation layer in Core.

Recommended structure:

```text
minx_mcp/core/interpretation/
    __init__.py
    models.py             -- typed request/response dataclasses or Pydantic models
    runner.py             -- common LLM invocation, schema enforcement, retries
    context.py            -- compact context builders per task
    goal_capture.py       -- goal interpretation policy + result validation
    finance_query.py      -- NL finance query translation
    import_detection.py   -- source-kind detection helpers
```

Responsibilities:

- build compact task-specific context
- render short structured prompts
- call the configured low-cost model
- normalize and validate structured output
- return typed plans back to deterministic code

This layer should not mutate the database and should not contain business persistence code.

## Reliability Rules For LLM Features

Every LLM-backed feature in this design must follow the same reliability pattern:

1. Build compact structured context
2. Require JSON-only schema-constrained output
3. Validate all returned fields deterministically
4. Reject unsupported fields or invalid values
5. Clarify rather than guess when ambiguity remains
6. Keep mutation as a separate explicit step

Additional reliability controls:

- set short request timeouts
- log malformed/low-confidence outputs
- fall back to deterministic clarify or `no_match` behavior
- keep prompts small enough for low-cost models
- avoid dumping full transaction history or full goal history when summary context is enough

## Finance Query Design

### New Structured Read Surface

Finance should expose richer deterministic read functions and server tools before or alongside NL querying.

Recommended additions:

- filtered transaction listing
- filtered spending total
- filtered transaction count
- optional date/category/merchant/account/description filters on sensitive reads

This allows both:

- direct structured clients to use the feature without LLMs
- natural-language clients to resolve into the same underlying deterministic path

### Query Plan Contract

The interpretation layer should target a typed internal query plan rather than calling SQL directly.

Example shape:

```json
{
  "intent": "list_transactions",
  "filters": {
    "start_date": "2026-03-01",
    "end_date": "2026-03-31",
    "merchant": "Whole Foods"
  },
  "confidence": 0.94,
  "needs_clarification": false
}
```

If ambiguous:

```json
{
  "intent": "list_transactions",
  "filters": {},
  "confidence": 0.51,
  "needs_clarification": true,
  "clarification_type": "ambiguous_merchant",
  "question": "Which merchant do you mean?",
  "options": ["Target", "Target Optical"]
}
```

### Why This Belongs In The Repo

This keeps business semantics inside Minx rather than pushing them into a harness.

It also creates a reusable pattern for later Meals and Training read queries.

## Importer Hardening Design

### Detection Pipeline

Importer detection should become a staged process:

1. deterministic file checks
2. lightweight content sampling
3. LLM classification into supported source kinds
4. deterministic validation of the detected kind
5. clarify path when uncertain

This should replace filename-first detection as the normal path, while still allowing explicit `source_kind` overrides.

### Clarify Instead Of Fail Blindly

When detection is uncertain, the system should return a structured clarification path that includes:

- columns found
- guessed source kind
- supported alternatives
- mapping requirement if relevant

This should make imports feel intelligent rather than brittle.

### Category Hint Wiring

`category_hint` data that is already extracted should be wired into import flow immediately.

First pass should use a deterministic best-effort strategy:

- if `txn.category_hint` exists
- fuzzy match against known finance category names
- assign category on insert when confidence is strong enough

This is a cheap deterministic improvement and should not wait for LLM rollout.

## Goal Capture Rewrite Design

`goal_capture` should be reimplemented as a policy wrapper over the shared interpretation layer.

The existing external contract should remain stable:

- same result types
- same explicit action proposals
- same no-mutate-by-default rule

The internal implementation should change from:

- regex extraction
- narrow trigger phrases
- exact-only subject mapping

to:

- schema-constrained intent extraction
- contextual resolution against active goals/categories/merchants
- deterministic validation and clarification synthesis

This preserves the current architectural decision that Core owns goal interpretation while dramatically improving real-language coverage.

## Deterministic Cleanup Included In This Design

Not all reliability gains should come from LLMs. This design explicitly includes deterministic cleanup where it is cheaper and more reliable.

### 1. Filtered Sensitive Finance Query

Extend the finance read/query path to support:

- `start_date`
- `end_date`
- `category_name`
- `merchant`
- `account_name`
- `description_contains`

This is useful on its own and is also the execution layer for NL queries.

### 2. Configurable Anomaly Threshold

Replace hardcoded anomaly threshold with a preference-backed value.

This should use:

- a reasonable default
- preference override support
- documentation in repo config/examples

### 3. File Refactors

The following files are likely to benefit from targeted splitting:

- `minx_mcp/core/goal_capture.py`
- `minx_mcp/finance/importers.py`
- `minx_mcp/finance/analytics.py`

The goal is not broad refactoring for its own sake. The goal is to reduce the chance that interpretation, query, and import changes become tangled.

### 4. Finance Service Connection Constraints

The current thread-local connection behavior should either be made more explicit in docs or gradually shifted toward a clearer request-scoped access pattern over time.

This is not urgent enough to block the feature work, but the constraint should stop being implicit.

## External References And What To Borrow

This design includes selective borrowing from existing projects.

### Actual Budget

Reference:

- [Actual rules documentation](https://actualbudget.org/docs/budgeting/rules/)
- [Actual repository](https://github.com/actualbudget/actual)
- [Actual API reference](https://actualbudget.org/docs/api/reference/)

Most useful ideas for Minx:

- ordered/staged transaction rules
- stronger payee normalization concepts
- import-time rule application
- dry-run import preview concept
- separating imported transaction data from canonicalized transaction interpretation

What to borrow in this design:

- staged rules in Phase 2
- import preview concept in Phase 2
- canonical merchant/payee normalization concepts in Phase 2

### actual-mcp

Reference:

- [actual-mcp repository](https://github.com/s-stefanov/actual-mcp)

Most useful ideas for Minx:

- richer finance MCP tool shapes
- filtered transaction and summary queries through MCP
- typed finance query surfaces rather than one broad raw detail tool

What to borrow in this design:

- stronger filtered finance query surface in Phase 1

### Firefly III Data Importer

Reference:

- [Firefly III Data Importer repository](https://github.com/firefly-iii/data-importer)

Most useful ideas for Minx:

- reusable import configuration
- clear separation of detect/map/import stages
- explicit correction or clarification when mapping is uncertain

What to borrow in this design:

- detect -> map -> import workflow shape
- clearer clarification path for uncertain imports

Code from Firefly should not be copied directly unless licensing implications are acceptable. The value here is workflow design, not direct code reuse.

### beangulp

Reference:

- [beangulp documentation](https://beancount.github.io/docs/beangulp.html)

Most useful ideas for Minx:

- importer `identify` vs `extract` separation
- stronger importer fixture and test posture
- clear normalized extracted-record contract

What to borrow in this design:

- importer pipeline concepts
- additional importer tests and fixture discipline

As with Firefly, treat this primarily as conceptual inspiration rather than direct code reuse.

### mcp-agent

Reference:

- [mcp-agent repository](https://github.com/lastmile-ai/mcp-agent)

Most useful ideas for Minx:

- structured outputs as default LLM integration posture
- shared context/config wiring
- observability around model calls

What to borrow in this design:

- shared interpretation runner patterns
- logging/metrics ideas for LLM-backed features

## Two-Phase Rollout

## Phase 1: Reliability And Query Foundations

Phase 1 should include:

- shared Core interpretation layer
- LLM-backed `goal_capture`
- LLM-backed import detection
- natural-language finance query translation
- richer deterministic finance query filters
- `category_hint` wiring
- anomaly threshold preference support
- targeted file refactors needed to keep the changes clean

### Phase 1 Outcome

After Phase 1:

- conversational goal capture feels much less brittle
- import detection is based on file content, not just naming
- finance supports conversational read queries safely
- key brittle low-level issues are resolved without broad unrelated refactors

## Phase 2: Finance Domain Maturity And Hardening

Phase 2 should include:

- staged finance rules inspired by Actual
- merchant canonicalization / alias support
- import preview / dry-run
- audit log surfacing
- interpretation observability and cost/error logging
- groundwork for event reconciliation or review reproducibility
- cleanup of service/context seams that will matter for future domains

### Phase 2 Outcome

After Phase 2:

- finance import and categorization feel meaningfully more mature
- Minx has better operational visibility into interpretation quality
- the repo is in a stronger position for Meals and future cross-domain growth

## Testing Strategy

This design requires heavy testing around the new interpretation boundary.

### Unit Tests

- LLM output normalization/validation
- malformed JSON handling
- unsupported field rejection
- low-confidence clarify paths
- finance NL query plan validation
- import detection result validation
- category hint wiring
- preference-backed anomaly threshold behavior

### Integration Tests

- `goal_capture` through actual Core server boundary
- finance NL query through actual MCP server path
- import detection with representative sample files
- explicit clarify flows when ambiguous

### Contract Tests

- existing MCP envelopes remain stable
- `goal_capture` output contract stays compatible
- finance read/query tools return stable structured shapes

### Cost And Reliability Tests

Where practical, interpretation tests should:

- use fixed stubbed model outputs
- verify fallback behavior on malformed responses
- verify no mutation occurs when interpretation is ambiguous

## Risks And Tradeoffs

### Risk: LLMs Increase Inconsistency

This is real if prompts are loose or outputs are trusted too much.

Mitigation:

- strict schema outputs
- deterministic validation
- clarify instead of guess
- explicit contract-level tests

### Risk: Cost Creep

This is real if prompts are too large or interpretation is used too broadly.

Mitigation:

- one low-cost model
- small prompts
- compact context building
- selective use only for interpretation tasks

### Risk: Overbuilding The Shared Layer

This is real if the interpretation framework becomes more abstract than the repo currently needs.

Mitigation:

- build only the parts required by goal capture, finance query, and import detection
- keep task-specific context builders close to their domains

## Recommended File Areas

Likely new or modified areas:

- `minx_mcp/core/interpretation/`
- `minx_mcp/core/server.py`
- `minx_mcp/core/goal_capture.py` or replacement modules
- `minx_mcp/finance/importers.py`
- `minx_mcp/finance/analytics.py`
- `minx_mcp/finance/server.py`
- `minx_mcp/finance/service.py`
- `minx_mcp/preferences.py`
- tests for Core interpretation, finance querying, and importer behavior

## Recommendation

Implement this design as one combined upgrade program with two implementation passes.

Phase 1 should land the interpretation foundation and direct reliability wins.
Phase 2 should borrow the best finance-domain maturity patterns from Actual and related references.

This keeps Minx aligned with its current architecture while substantially improving the areas that are most likely to frustrate real use.
