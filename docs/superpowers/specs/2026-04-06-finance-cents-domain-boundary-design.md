# Finance Cents + Domain Boundary Design

**Date:** 2026-04-06
**Status:** Approved for planning
**Scope:** Complete the remaining finance data cleanup and define the reusable multi-domain seam for future domains

## Goal

Finish the two deferred follow-up features from the finance/contracts cleanup:

- migrate finance money storage from `REAL` dollars to integer cents
- define a reusable, future-ready domain boundary and cross-domain composition seam without prematurely scaffolding `health` or `meals`

## Success Criteria

This design is successful when:

- finance stores money as integer cents in SQLite
- finance business logic and aggregation use exact integer arithmetic internally
- MCP responses and generated reports continue to present dollar amounts for compatibility and readability
- the codebase has a clear reusable seam for future domains that separates per-domain boundaries from cross-domain composition
- finance becomes the first implementation of that seam without being refactored into a speculative framework

## Non-Goals

This design does not include:

- implementing `health`, `meals`, or dashboard features
- changing the current MCP response envelope defined in `contracts.py`
- redesigning transport bootstrapping
- introducing runtime plugin discovery or automatic domain registration
- changing user-visible finance outputs from dollars to integer cents

## Product Decisions

The following decisions are locked in for this slice:

- Money is stored and computed as integer cents internally.
- Finance MCP tools and finance markdown reports continue to return dollar-formatted values at the boundary.
- Shared money conversion logic is centralized so future domains can reuse the same pattern for exact internal values plus presentation-oriented outputs.
- The multi-domain seam is split into a boundary contract module and a composition module.
- `finance` is the first real implementation of that seam.
- The seam is future-ready only. No placeholder `health` or `meals` packages are added in this pass.

## Why This Design

### Why Integer Cents Internally

The precision problem exists in persistence and aggregation, not in display. SQLite `REAL` values are susceptible to floating-point drift, which is the wrong tradeoff for financial imports, dedupe fingerprints, comparisons, and summed totals.

Migrating to integer cents fixes the correctness problem at the source:

- inserts become exact
- sums and comparisons become exact
- anomaly thresholds become exact
- dedupe fingerprints stop depending on float formatting behavior

Keeping external outputs in dollars is still the right choice for this pass:

- it avoids a second breaking change while the storage migration is landing
- it keeps reports human-readable
- it preserves the current MCP-facing shape for existing consumers

This establishes an intentional boundary:

- domain/storage layers own exact values
- transport/report layers own presentation

### Why Split The Reusable Seam

A single catch-all `cross_domain.py` would be easy to start but likely to accumulate unrelated concerns: shared contracts, adapters, registries, read models, and future orchestration helpers. That would make the boundary less clear over time.

Instead, this pass separates:

- domain boundary contracts, which define what an individual domain can expose
- cross-domain composition, which defines how multiple domains get combined

This is a better fit for the current repo because there is only one implemented domain today. The codebase needs a small honest interface that finance can implement now, not a broad framework built around hypothetical future needs.

The separation also creates a cleaner future dashboard seam:

- dashboard-like consumers depend on generic overview/read models
- domains keep owning their own business logic and writes
- composition code never needs to know finance internals beyond the boundary contract

## Architecture Overview

This design introduces two distinct shared modules:

- `minx_mcp/money.py`
  - exact money parsing, storage conversion, formatting, and serialization helpers
- `minx_mcp/domain_boundary.py`
  - shared domain-facing typed protocols and dataclasses for read-oriented domain outputs
- `minx_mcp/cross_domain.py`
  - helpers that compose multiple domain providers into cross-domain overview payloads

Finance remains responsible for finance-specific behavior in:

- `minx_mcp/finance/service.py`
- `minx_mcp/finance/analytics.py`
- `minx_mcp/finance/reports.py`
- `minx_mcp/finance/server.py`

Finance also gains a thin read adapter:

- `minx_mcp/finance/overview.py`
  - converts finance summary data into shared cross-domain read models

## Money Model

### Internal Representation

The finance domain should treat money as integer cents once normalized.

Examples:

- `$12.34` becomes `1234`
- `-$42.16` becomes `-4216`
- `$0.00` becomes `0`

SQLite storage changes:

- `finance_transactions.amount` is replaced by `finance_transactions.amount_cents INTEGER NOT NULL`

Queries and write paths should stop reading or writing raw floating-point amounts in domain storage.

### Boundary Representation

At MCP and markdown boundaries, dollar values remain the user-facing form.

That means:

- finance MCP responses continue to expose amounts such as `-42.16`
- finance report summaries continue to render standard dollar strings
- internal cents are converted once at the output edge

### Shared Money Helpers

Add a shared `minx_mcp/money.py` module with narrow helpers such as:

- `parse_dollars_to_cents(value: str | float | int) -> int`
- `cents_to_dollars(value: int) -> float`
- `format_cents(value: int) -> str`

Responsibilities:

- parse importer outputs into exact cents
- keep rounding rules centralized
- avoid repeated ad hoc `round(..., 2)` or `float(...)` logic throughout finance

The finance domain should not contain duplicated cent-conversion code in parsers, analytics, reports, and tests.

## Database Migration Design

### New Migration

Add a new migration:

- `004_finance_amount_cents.sql`

Responsibilities:

- add `amount_cents` to `finance_transactions`
- backfill it from the current `amount` column using cent conversion
- preserve all existing rows
- rebuild any dependent views to reference `amount_cents`
- remove the legacy `amount` column by table rebuild, since SQLite does not support a simple in-place type change

### Migration Strategy

Use a table-rebuild migration so the final schema is clean rather than permanently carrying both columns.

Recommended shape:

1. create a replacement transactions table with `amount_cents INTEGER NOT NULL`
2. copy rows from the current table with deterministic cent conversion
3. recreate indexes and foreign-key references as needed
4. swap the new table into place
5. recreate dependent views against the new column

### Backfill Rule

Backfill should use deterministic two-decimal conversion:

- convert stored dollar values to cents with one rounding step
- cast the result to integer

This is intentionally a one-time migration rule. After migration, new writes should avoid floats entirely.

## Finance Code Changes

### Parsers And Importers

Finance parsers currently normalize imported transactions with `amount` as dollars. This pass should change the normalized transaction contract to use `amount_cents`.

Normalized importer output becomes:

```python
{
    "posted_at": "2026-03-28",
    "description": "HEB",
    "amount_cents": -4216,
    "merchant": "HEB",
    "category_hint": "groceries",
    "external_id": "optional-stable-id",
}
```

Importer and parser changes:

- parse source values into cents immediately
- stop emitting float amounts from parser modules
- update importer-side validation and downstream insert logic to require `amount_cents`

### Service Layer

`minx_mcp/finance/service.py` should:

- insert `amount_cents`
- compare and validate against `amount_cents`
- keep typed contract errors exactly as introduced in the prior cleanup

`finance_transaction_dedupe` fingerprints should incorporate integer cents instead of formatted floats.

### Analytics Layer

`minx_mcp/finance/analytics.py` should aggregate in cents and convert only for returned payloads.

That means:

- `SUM(amount_cents)` internally
- anomaly thresholds represented as cents
- returned summaries continue to expose dollars

### Reports Layer

`minx_mcp/finance/reports.py` should aggregate in cents internally and format at the edge.

This includes:

- weekly inflow/outflow
- category totals
- merchant totals
- month-over-month deltas
- anomaly payloads
- markdown rendering

The current report and MCP payload shapes remain stable; only the internal arithmetic changes.

## Reusable Domain Seam

### Domain Boundary Module

Add `minx_mcp/domain_boundary.py`.

This module defines the reusable contracts that an individual domain can implement. Initial types should stay small and read-oriented, for example:

- `DomainDescriptor`
  - stable domain metadata such as domain name and display label
- `OverviewCard`
  - a generic read model for a dashboard or overview consumer
- `OverviewProvider`
  - a protocol that can describe the domain and return overview cards

Responsibilities:

- provide typed read-model contracts
- remain transport-agnostic
- avoid ownership of domain writes, jobs, transport wiring, or persistence rules

### Cross-Domain Composition Module

Add `minx_mcp/cross_domain.py`.

This module composes multiple providers into one cross-domain read payload, for example:

- `build_overview(providers: Iterable[OverviewProvider]) -> list[OverviewCard]`

Responsibilities:

- combine provider outputs
- enforce stable ordering or simple composition conventions
- remain unaware of finance-specific query details

This module should not become a registry, plugin system, or dumping ground for unrelated shared types.

### Finance Adapter

Add `minx_mcp/finance/overview.py`.

This module adapts finance’s existing summary/reporting data into shared boundary types.

Responsibilities:

- call finance-owned summary logic
- translate finance summary output into `OverviewCard`
- avoid re-implementing finance business logic

This makes finance the reference implementation for future domains while keeping the seam honest and small.

## Boundary Rules

To keep responsibilities clear:

- `contracts.py` owns MCP success/failure envelopes only
- `money.py` owns exact money conversion and formatting helpers
- `domain_boundary.py` owns reusable domain-facing contracts
- `cross_domain.py` owns read-only composition across domains
- `finance/*` owns finance business rules and writes
- `finance/server.py` continues to own MCP tool registration and transport-facing validation

This separation avoids a shared-module blob and keeps future domain work incremental.

## Testing Strategy

Tests should prove both migration safety and seam durability.

### Money Migration Tests

Add or update tests to verify:

- database bootstrap applies the new migration
- pre-migration-style data backfills into exact cents
- imported transactions are stored as integer cents
- analytics totals remain correct after conversion
- reports still return the same dollar-facing numbers
- dedupe remains stable when equivalent dollar inputs are imported repeatedly

### Domain Seam Tests

Add tests to verify:

- `OverviewCard` and `OverviewProvider` composition works with finance
- `cross_domain.build_overview(...)` combines providers predictably
- finance overview adapters return stable generic read models without leaking finance internals

## Rollout Plan

Implement in this order:

1. Add the shared money helper module.
2. Add a failing migration/bootstrap test for the new cents schema.
3. Add the new finance migration and backfill logic.
4. Update parsers, importers, service inserts, dedupe, analytics, and reports to use `amount_cents`.
5. Update finance tests to assert cents internally and dollars externally.
6. Add `domain_boundary.py` and `cross_domain.py`.
7. Add the finance overview adapter and tests for the new seam.
8. Run the full test suite and confirm no MCP envelope behavior regressed.

## Risks And Mitigations

### Risk: Silent Rounding Drift During Migration

Mitigation:

- use one deterministic backfill rule
- add migration tests with positive and negative sample amounts
- stop using floats for new writes immediately after the migration lands

### Risk: Breaking Existing Consumers

Mitigation:

- preserve dollar-facing MCP/report payloads
- keep envelope structure unchanged
- limit outward changes to internal correctness improvements

### Risk: Over-Engineering The Multi-Domain Layer

Mitigation:

- keep the shared seam read-only and small
- implement only the types finance can use immediately
- avoid registries, discovery, and placeholder domains until a real second domain exists
