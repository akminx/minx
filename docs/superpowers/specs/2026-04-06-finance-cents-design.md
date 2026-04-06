# Finance Cents Migration Design

**Date:** 2026-04-06
**Status:** Revised after review
**Scope:** Migrate finance money storage and arithmetic from `REAL` dollars to integer cents without changing MCP envelope behavior or dollar-facing outputs

## Goal

Remove floating-point money storage from the finance domain by migrating persistence, write paths, and aggregation to integer cents while preserving current MCP response shapes and human-readable report output.

## Success Criteria

This design is successful when:

- `finance_transactions` stores money as integer cents
- finance business logic and aggregation use exact integer arithmetic internally
- MCP responses and generated reports continue to present dollar amounts for compatibility and readability
- dedupe fingerprints stop depending on float formatting behavior
- analytics and reporting behavior remain functionally unchanged apart from improved precision

## Non-Goals

This design does not include:

- implementing a reusable multi-domain seam
- implementing `health`, `meals`, or dashboard features
- changing the MCP response envelope defined in `contracts.py`
- redesigning transport bootstrapping
- changing user-visible finance outputs from dollars to integer cents

## Product Decisions

The following decisions are locked in for this slice:

- Money is stored and computed as integer cents internally.
- Finance MCP tools and finance markdown reports continue to return dollar-formatted values at the boundary.
- Shared money conversion logic is centralized in `minx_mcp/money.py`.
- Runtime parsing rejects values with more than two decimal places instead of silently rounding them.
- The future multi-domain seam is deferred until a second domain or a real cross-domain consumer exists.

## Why This Design

The precision problem exists in persistence and aggregation, not in display. SQLite `REAL` values are vulnerable to floating-point drift, which is the wrong tradeoff for financial imports, dedupe fingerprints, comparisons, and summed totals.

Migrating to integer cents fixes the correctness problem at the source:

- inserts become exact
- sums and comparisons become exact
- anomaly thresholds become exact
- dedupe fingerprints stop depending on float formatting behavior

Keeping external outputs in dollars is still the right choice for this pass:

- it avoids a second breaking change while the storage migration is landing
- it keeps reports human-readable
- it preserves the current MCP-facing shape for existing consumers

This creates a clean boundary:

- domain and storage layers own exact values
- MCP and markdown layers own presentation

## Architecture Overview

This design introduces one shared module:

- `minx_mcp/money.py`
  - exact money parsing, storage conversion, formatting, and serialization helpers

Finance remains responsible for finance-specific behavior in:

- `minx_mcp/finance/service.py`
- `minx_mcp/finance/analytics.py`
- `minx_mcp/finance/reports.py`
- `minx_mcp/finance/server.py`
- `minx_mcp/finance/importers.py`
- `minx_mcp/finance/parsers/*`

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

- `parse_dollars_to_cents(value: str) -> int`
- `cents_to_dollars(value: int) -> float`
- `format_cents(value: int) -> str`

Responsibilities:

- parse importer values into exact cents
- keep validation and conversion rules centralized
- avoid repeated ad hoc `round(..., 2)` or `float(...)` logic throughout finance

Parsing rule:

- runtime parser inputs must represent at most two fractional decimal places
- values with more than two decimal places are rejected as invalid input instead of being silently rounded
- parser code should operate on source text and convert to cents directly, not parse to float first

This keeps new imports exact even though the migration must deal with pre-existing `REAL` rows.

## Database Migration Design

### New Migration

Add a new migration:

- `004_finance_amount_cents.sql`

Responsibilities:

- replace `finance_transactions.amount REAL NOT NULL` with `finance_transactions.amount_cents INTEGER NOT NULL`
- backfill existing rows from the legacy `amount` column
- preserve all existing rows
- recreate indexes and dependent views
- remove the legacy `amount` column by table rebuild, since SQLite does not support a simple in-place type change

### Migration Strategy

Use a table-rebuild migration so the final schema is clean rather than permanently carrying both columns.

Recommended shape:

1. create a replacement transactions table with `amount_cents INTEGER NOT NULL`
2. copy rows from the current table using the explicit backfill expression
3. recreate indexes
4. swap the new table into place
5. recreate dependent views against the new column

Fresh database bootstraps will still apply `002_finance.sql` and then `004_finance_amount_cents.sql`. That is an acceptable short-term tradeoff for keeping migration history linear and safe.

### Backfill Rule

Backfill uses this exact SQLite expression:

```sql
CAST(ROUND(amount * 100, 0) AS INTEGER)
```

Rounding semantics:

- round to the nearest cent
- half values round away from zero
- examples: `12.345 -> 1235`, `-12.345 -> -1235`

This is a one-time migration rule for legacy `REAL` data. After migration, new writes should avoid floats entirely.

### Dependent Views

The migration must explicitly recreate:

- `v_finance_monthly_spend`

The recreated view should aggregate `amount_cents` internally and, if needed for compatibility, expose dollar-facing totals by converting at the view boundary or in consumers. The implementation choice should be made in the plan, but the dependency must be called out explicitly.

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
- reject source amounts that cannot be represented exactly to the cent

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
- `ANOMALY_THRESHOLD = -25000`
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

## Future Multi-Domain Composition

This pass intentionally defers `domain_boundary.py`, `cross_domain.py`, and any finance overview adapter.

Reasoning:

- there is only one implemented domain today
- there is no real cross-domain consumer yet
- any shared overview abstraction designed now would be speculative and likely to calcify before it is validated

When a second domain or a real dashboard-style consumer is in active planning, the cross-domain seam should be designed against at least two concrete cases instead of one.

## Testing Strategy

Add or update tests to verify:

- database bootstrap applies the new migration
- pre-migration-style data backfills into exact cents using the explicit rounding rule
- imported transactions are stored as integer cents
- analytics totals remain correct after conversion
- reports still return the same dollar-facing numbers
- dedupe remains stable when equivalent dollar inputs are imported repeatedly
- `v_finance_monthly_spend` is recreated and remains queryable after migration

## Rollout Plan

Implement in this order:

1. Add the shared money helper module.
2. Add failing migration and bootstrap tests for the new cents schema and backfill behavior.
3. Add the new finance migration and recreate `v_finance_monthly_spend`.
4. Update parsers, importers, service inserts, dedupe, analytics, and reports to use `amount_cents`.
5. Update finance tests to assert cents internally and dollars externally.
6. Run the full test suite and confirm no MCP envelope behavior regressed.

## Risks And Mitigations

### Risk: Silent Rounding Drift During Migration

Mitigation:

- use one explicit backfill expression
- add migration tests with positive and negative half-cent examples
- stop using floats for new writes immediately after the migration lands

### Risk: Breaking Existing Consumers

Mitigation:

- preserve dollar-facing MCP and report payloads
- keep the envelope structure unchanged
- limit outward changes to internal correctness improvements

### Risk: Mixed Internal Conventions During Refactor

Mitigation:

- make `amount_cents` the only normalized money field after this migration
- remove remaining float-based finance helpers and tests as part of the same change
- centralize conversions in `money.py`
