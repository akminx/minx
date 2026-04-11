**Status: Implemented (historical).** This spec was implemented in an earlier slice. The codebase has since evolved — see [HANDOFF.md](/Users/akmini/Documents/minx-mcp/HANDOFF.md) for current state.

# Minx Current Domains Hardening And Finance Maturity Design

**Date:** 2026-04-09
**Status:** Drafted for implementation
**Scope:** Core + Finance + Goals only
**Parent:** [2026-04-06-minx-life-os-architecture-design.md](2026-04-06-minx-life-os-architecture-design.md)
**Related:** [2026-04-09-llm-reliability-and-finance-hardening-design.md](2026-04-09-llm-reliability-and-finance-hardening-design.md)

## Goal

Bring the current Minx domains to a point where they are trustworthy, cleanly structured, observable, and comfortable enough to move on from without reopening Core again before later domains.

This design is intentionally limited to the domains Minx already has:

- Core shared interpretation and MCP boundaries
- Finance imports, reads, rules, monitoring, and reporting
- Goals capture, persistence, progress, and finance-linked review support

The outcome should be:

- architecture clean enough for future domains to follow without Core churn
- Finance useful enough for everyday personal tracking of income, expenses, categories, patterns, and goal support
- Goals reliable enough to capture, track, and review without brittle behavior
- observability strong enough that failures and strange model behavior are diagnosable

## Why This Work Matters Now

The current repo already has solid local-first fundamentals, but the present baseline is still not "comfortable enough to move on" for three reasons:

1. some of the newly added interpretation behavior is only partially wired or only covered in local tests rather than through real tool boundaries
2. Finance is becoming more capable, but imports, merchant identity, rules, and monitoring are not yet strong enough to fully trust as a daily system
3. the docs and roadmap language currently imply a deeper maturity level than the code has actually earned

This design defines a truthful finish line for the current domains before later domains are introduced.

## Product Intent

For Finance, Minx should help one person:

- track expenses and incoming income
- see how much is spent on which categories and merchants
- develop reusable patterns and rules from that behavior
- monitor finances over time
- support the goals they set rather than merely store those goals

For the broader project, Minx should favor:

- clean architecture
- reliable code and tool contracts
- efficient local-first execution
- UX that clarifies before making risky assumptions

## Success Criteria

This design is successful when:

- the current review findings are fixed at the real tool/service boundaries
- Core interpretation is production-real rather than half-wired
- all sensitive finance reads, including aggregate reads, are auditable
- imports are trustworthy enough for normal use
- merchant/category behavior is stable enough that spending insights are meaningful
- goal capture, goal tracking, and finance-linked goal review are dependable
- the code organization is clean enough that later domains can follow the same shape
- the docs/specs/HANDOFF state exactly what is shipped and what is still deferred

## Non-Goals

This design does not attempt to:

- add new domains such as Meals or Training
- implement a generalized agent runtime or autonomy layer
- turn Minx into a full envelope-budgeting system
- copy another project's data model wholesale
- solve every future trust-policy or harness-specific integration concern

## Core Design Principles

The project should keep and strengthen these rules:

- LLMs interpret
- deterministic code validates
- deterministic code executes

And structurally:

- Core owns shared interpretation contracts and runners
- Finance owns imported facts, transaction cleanup, rules, monitoring, and read semantics
- Goals owns goal truth, goal progress, and goal-facing finance linkage
- MCP/server layers stay thin
- read models and projections may improve UX, but they do not own truth

## Required Correctness Fixes

The following are mandatory in Phase 1. They are not optional cleanup.

### 1. Real LLM Goal Capture Wiring

The LLM-backed `goal_capture` path must be reachable through the actual Core MCP tool, not only through unit-level helpers.

Required outcome:

- `create_core_server` and `_goal_capture` can use a configured JSON-capable LLM
- docs and tests reflect the actual shipped behavior
- if the LLM is unavailable or malformed, behavior degrades safely and explicitly

### 2. Async-Safe Interpretation Boundaries

Interpretation helpers must not call `asyncio.run(...)` inside library-level functions.

Required outcome:

- interpretation APIs are async where appropriate
- MCP/tool boundaries await them correctly
- the shared interpretation layer is safe to call from already-running event loops

### 3. Stronger Finance Query Validation

The finance query/read path must reject invalid input instead of silently degrading.

Required outcome:

- reversed date windows are rejected
- blank or whitespace-only optional text filters are rejected
- clarify and invalid-input paths are explicit and tested

### 4. Complete Sensitive-Read Auditing

All sensitive finance reads, including aggregate `sum_spending` and `count_transactions`, must be logged through the same audit posture as row-level reads.

Required outcome:

- aggregate reads produce audit entries
- tests pin the behavior
- the sensitive tool surface is internally consistent

### 5. Interpretation Contract Tightening

Interpretation schemas should reject malformed clarify payloads at validation time instead of allowing later internal runtime errors.

Required outcome:

- schema-level consistency for clarify results
- no internal-error path caused by obviously inconsistent model output

## External References And What To Borrow

This design uses external references selectively. The rule is not "port the repo." The rule is "borrow only behavior that already fits Minx's product vision."

### Actual Budget

References:

- [Rules](https://actualbudget.org/docs/budgeting/rules/)
- [Payees](https://actualbudget.org/docs/transactions/payees/)
- [Importing Transactions](https://actualbudget.org/docs/transactions/importing/)
- [API Reference](https://actualbudget.org/docs/api/reference/)

Useful behaviors to borrow:

- rules that operate over imported transaction cleanup
- strong separation between `imported_payee` and cleaned canonical payee identity
- merchant/payee cleanup before categorization and reporting
- automatic support for categorization and rename learning as a later maturity target
- duplicate-resistant import behavior based on stable identifiers and fuzzy matching

What Minx should borrow now:

- staged rule pipeline concepts
- merchant normalization / aliasing concepts
- distinction between raw imported merchant text and canonical merchant identity
- spending rollups and category/merchant trend views that support personal finance monitoring

What Minx should not borrow:

- envelope-budgeting philosophy
- large budgeting UI concepts
- anything that distorts Minx into "Actual but smaller"

### actual-mcp

Reference:

- [actual-mcp repository](https://github.com/s-stefanov/actual-mcp)

Useful behaviors to borrow:

- typed finance tool shapes instead of one broad raw detail tool
- filtered transaction and reporting reads over MCP
- explicit accounts/transactions/categories/rules surfaces

What Minx should borrow now:

- stronger typed finance MCP surface
- filtered reads by category, merchant, account, date, and description
- conversational query translation that resolves into typed deterministic query plans

What Minx should not borrow:

- API surface breadth for breadth's sake
- CRUD expansion that does not directly support Minx's current personal finance workflow

### Firefly III Data Importer

References:

- [Firefly III Data Importer repository](https://github.com/firefly-iii/data-importer)
- [Firefly documentation entry](https://docs.firefly-iii.org/)

Useful behaviors to borrow:

- detect -> map -> preview -> import workflow shape
- reusable import configuration
- explicit clarification and correction when import mapping is uncertain
- import safety before persistence

What Minx should borrow now:

- import preview / dry-run
- clearer uncertain-import clarification
- explicit mapping validation surface for generic CSVs

What Minx should not borrow:

- Firefly-specific infrastructure shape
- code copied directly from the importer

### beangulp

References:

- [Beangulp](https://beancount.github.io/docs/beangulp.html)
- [Importing External Data](https://beancount.github.io/docs/importing_external_data.html)

Useful behaviors to borrow:

- importer `identify` vs `extract` separation
- importer-as-library discipline
- strong importer fixture and expected-output testing
- explicit handling of downloaded source files as durable artifacts

What Minx should borrow now:

- clearer source identification vs extraction boundaries
- stronger importer fixture coverage
- preview/test posture that makes importer regressions obvious

What Minx should not borrow:

- Beancount ledger semantics
- bean-file style document filing as a core product concern

### mcp-agent

Reference:

- [mcp-agent repository](https://github.com/lastmile-ai/mcp-agent)

Useful behaviors to borrow:

- structured outputs as the default posture for LLM integration
- shared runner/config pattern for model-backed features
- observability around model calls

What Minx should borrow now:

- shared interpretation runner discipline
- structured-output-first model integration
- redacted logging and metrics for interpretation calls

What Minx should not borrow:

- heavy workflow runtime
- durable orchestration architecture
- generalized agent framework behavior as a dependency of current-domain work

## Target Product Shape

### Finance

Finance should support a trustworthy workflow:

1. import data safely
2. preview or clarify uncertain imports before persistence
3. normalize merchants and preserve raw import text
4. apply deterministic staged rules
5. surface clean read/query/report views
6. monitor category, merchant, income, and anomaly trends
7. support active goals with pacing and risk signals

### Goals

Goals should support:

- reliable capture through actual tool boundaries
- explicit create/update/clarify/no-match semantics
- progress derived from deterministic finance reads
- goal-facing insights that help action, not just storage

### Core

Core should provide:

- shared interpretation contracts and runners
- async-safe boundaries
- observability for interpretation behavior
- stable MCP contracts and explicit validation behavior

## Phases

### Phase 1: Correctness And Foundation Hardening

This is the required "comfortable enough to move on" phase.

Includes:

- fix the review findings
- wire LLM-backed `goal_capture` through the real server path
- make interpretation APIs async-safe
- strengthen finance query validation and auditing
- tighten interpretation schema consistency
- improve observability/logging for model-backed interpretation
- align docs/HANDOFF/specs with shipped behavior
- add missing boundary tests for the new query and interpretation flows

Expected outcome:

- current domains are truthful, stable, and debuggable
- architecture is clean enough for later domains to copy the patterns

### Phase 2: Finance Maturity

This is the high-value finance improvement phase that still clearly fits the current project vision.

Includes:

- merchant normalization / aliasing
- staged finance rules
- import preview / dry-run
- stronger import clarification paths
- better filtered reporting and monitoring views
- more explicit distinction between raw imported merchant text and canonical merchant identity

Expected outcome:

- finance data becomes materially cleaner
- categorization and monitoring become significantly more trustworthy
- imports become safer and more understandable

### Phase 3: Goal-Finance Intelligence

This phase turns finance tracking into actual goal support.

Includes:

- goal-supporting insights
- pacing / risk signals
- category / merchant / account trend views framed in goal terms
- recurring income and expense pattern surfacing
- review-facing projections that highlight what matters for active goals

Expected outcome:

- Minx helps the user hit goals instead of merely storing them

## Architecture And Code Shape

### Shared Interpretation Layer

The interpretation layer should remain in Core, but it should become more complete and more disciplined.

Target structure:

```text
minx_mcp/core/interpretation/
    __init__.py
    models.py
    runner.py
    context.py
    goal_capture.py
    finance_query.py
    import_detection.py
    logging.py
```

Responsibilities:

- compact task-specific context builders
- structured prompt rendering
- shared model invocation
- schema-constrained output validation
- redacted observability/logging

This layer must not:

- mutate the database
- execute SQL directly
- own product truth for Finance or Goals

### Finance Domain Structure

Finance should move toward clearer responsibilities:

- `import_detection` identifies source kind
- importer/parser code extracts normalized raw transaction records
- normalization converts merchant/raw import text into canonical forms
- rules apply deterministic cleanup and categorization
- service code persists and exposes domain operations
- reporting/monitoring code produces read models and summaries

### Goal Domain Structure

Goals should keep:

- explicit persistence/service logic in Goals
- deterministic progress building from Finance read APIs
- interpretation and goal capture policy in Core

Goals should not absorb Finance cleanup logic.

## Observability And Auditability

The current domains should gain enough visibility that unexpected behavior is explainable.

Required:

- audit logs for all sensitive finance query intents
- interpretation failure logging with redacted prompt context
- explicit reasons for clarify outcomes where practical
- rule-match and import-preview summary surfaces

Preferred:

- counts of malformed model responses
- low-confidence interpretation counters
- importer detection confidence/clarify metrics

## UX Direction

The user-facing posture for the current domains should be:

- clarify instead of guessing
- preview before risky import persistence
- show clean merchant/category names rather than raw noisy imports where possible
- keep tool contracts typed and stable
- explain finance-goal risk in practical language

This is a trust-heavy domain. "Mostly right" is not good enough UX.

## Acceptance Criteria

Phase 1 is done when:

- all known review findings are fixed and covered by tests
- `goal_capture` LLM behavior is reachable through the actual Core tool boundary
- no interpretation helper uses nested event-loop execution
- finance query/date/filter validation is explicit and contract-shaped
- all sensitive finance query intents are audited
- docs and handoff text match the shipped behavior
- full repo tests and mypy are green

Phase 2 is done when:

- merchants can be normalized/aliased deterministically
- staged rules run predictably and are tested
- preview/import clarification exists for uncertain imports
- monitoring surfaces expose category/merchant/income patterns clearly

Phase 3 is done when:

- goal-supporting insights are useful enough to change user behavior
- active goals can be monitored through finance trends and pacing views

## Intentional Deferrals

The following remain out of scope for this design:

- new domains
- generalized autonomy
- dashboard-heavy product work
- full trust engine or auth model
- harness-specific client adaptation work

## Recommended Next Step

After this spec is approved, the next step should be a concrete implementation plan that:

- starts with Phase 1 only
- treats the current review findings as batch-zero correctness work
- leaves Phases 2 and 3 in the same plan as later batches rather than separate disconnected efforts
