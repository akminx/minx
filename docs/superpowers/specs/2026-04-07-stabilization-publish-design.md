# Minx MCP Stabilization And Publish Design

**Date:** 2026-04-07
**Status:** Proposed
**Branch:** `codex/slice1-event-pipeline-daily-review`

## Goal

Stabilize `minx-mcp` into a genuinely usable state for local operation, then publish only that cleaned, verified state to GitHub `main`.

This pass should improve real runtime correctness and maintainability without turning into a full architecture rewrite. The intended result is a repo that can be checked out, installed, started, and used for the core finance and daily-review flows with clear operational behavior.

## Non-Goals

- Rewriting the entire finance domain around new abstractions
- Changing MCP response shapes unless required for correctness
- Broad refactors outside the finance report/import/review paths
- Building production-grade distributed durability semantics across SQLite and the filesystem

## Current Problems

### 1. Report generation has half-committed semantics

`FinanceService.generate_weekly_report()` and `generate_monthly_report()` currently write the markdown file before durable database bookkeeping completes. If later persistence fails, the database can roll back while the vault file remains on disk.

That means the current implementation can leave ambiguous state:

- vault file exists
- `finance_report_runs` row may not exist
- `finance.report_generated` event may not exist

This is the main correctness issue blocking a clean publish.

### 2. Reports are too stringly-typed

`minx_mcp/finance/reports.py` still builds and transforms repeated `dict[str, object]` structures. This makes the code harder to reason about, easier to break during refactors, and more "AI gluey" than the rest of the repo.

### 3. Import/parser internals are too loose

`minx_mcp/finance/importers.py` and parser modules still pass around weakly typed parsed payloads and mappings. This leaks ad hoc dict contracts into service logic and makes validation/reactive cleanup harder than it needs to be.

### 4. Publish state is noisy

The repo has working code, but the working tree also contains WIP docs and handoff material that should not automatically be pushed as the public, stable face of the project.

## Desired Outcome

After this pass:

- report generation has explicit, recoverable lifecycle semantics
- report and import internals use typed internal models instead of loose dict bundles
- the outward MCP behavior stays stable unless a bug forces change
- the repo has a clean, intentional publish state
- a clean checkout can be installed, tested, started, and used for the main flows

## Design Principles

### Bound the cleanup

This is a stabilization pass, not a rewrite. Cleanup should be aggressive where it improves correctness, type safety, and operator confidence, and conservative everywhere else.

### Keep public behavior stable

Typed cleanup should happen inside finance internals first. Existing MCP tool response shapes and basic service return shapes should stay stable where practical so the publish does not create unnecessary churn.

### Make state explicit when true atomicity is impossible

Report generation cannot be truly atomic across SQLite and the filesystem. The design should not hide that fact. Instead, it should model report lifecycle state explicitly and make repair/retry behavior deterministic.

## Architecture

### Existing boundaries to preserve

- `FinanceService` remains the orchestration boundary for finance operations.
- MCP tool wiring in `minx_mcp/finance/server.py` remains thin and stable.
- `VaultWriter` remains the filesystem boundary.
- `minx_mcp/core/` remains the home for daily review orchestration, detectors, read models, and LLM seams.

### New internal model boundaries

Add small typed internal model modules:

- `minx_mcp/finance/report_models.py`
  - weekly/monthly report dataclasses
  - typed totals, rollups, deltas, recurring highlights, and review items
- `minx_mcp/finance/import_models.py`
  - parsed transaction dataclass
  - parsed import batch dataclass
  - typed generic CSV mapping model if it simplifies parser boundaries

These models are internal implementation contracts. They do not force immediate changes to outward MCP envelopes.

## Report Lifecycle Design

### Why simple "atomic file write" is not enough

Atomic temp-file-plus-rename write semantics are good and should be used, but they do not solve the whole problem. A crash or SQLite failure after the rename can still leave a visible file without durable DB confirmation.

So the system needs both:

- atomic file replacement at the vault boundary
- explicit report-run lifecycle state in the database

### Report run state model

Evolve `finance_report_runs` so one logical report window has one authoritative row.

Recommended fields:

- existing logical identity:
  - `report_kind`
  - `period_start`
  - `period_end`
- artifact data:
  - `vault_path`
  - `summary_json`
- lifecycle fields:
  - `status` with values `pending`, `completed`, `failed`
  - `updated_at`
  - `error_message` nullable

Add a unique index on:

- `(report_kind, period_start, period_end)`

This changes the table from "append-only history of attempts" to "current authoritative state for this logical report window." That is the right trade-off for a local operational tool where deterministic repair matters more than preserving every transient attempt.

### Report generation flow

For both weekly and monthly report generation:

1. Validate the date window.
2. Build the typed report summary.
3. Render markdown from the typed model.
4. Upsert the report row as `pending` with the intended `vault_path` and serialized summary.
5. Write the markdown atomically through `VaultWriter`.
6. In one database transaction:
   - emit `finance.report_generated`
   - mark the report row `completed`
   - clear any prior `error_message`
7. If any step after file write fails:
   - best-effort remove the newly written file
   - mark the report row `failed` with an `error_message`
   - re-raise the error to the caller

### Retry behavior

Rerunning report generation for the same logical window should repair prior ambiguous state:

- `pending` row: overwrite and continue
- `failed` row: overwrite and continue
- `completed` row: overwrite deterministically with the new result

This keeps one report window idempotent and inspectable.

## Vault Writer Changes

Extend `VaultWriter` with an atomic markdown write path based on writing a temp file in the target directory and renaming it into place.

Required behavior:

- target path resolution rules remain unchanged
- writes stay within allowed roots
- parent directories are created if missing
- replacement is atomic on the local filesystem

This should be exposed as the default behavior for markdown writes rather than an optional special case, unless tests show that a narrower API is cleaner.

## Report Typing Design

Refactor `minx_mcp/finance/reports.py` around typed internal models instead of dict assembly.

### Weekly report model

The weekly report summary should contain typed fields for:

- period metadata
- totals
- top categories
- notable merchants
- category changes vs prior week
- anomalies
- uncategorized transactions

### Monthly report model

The monthly report summary should contain typed fields for:

- period metadata
- account rollups
- category totals
- changes vs prior month
- recurring charge highlights
- anomalies
- uncategorized or newly seen merchants

### Serialization boundary

Typed report models may still be converted to plain dicts when:

- returning service results
- storing `summary_json`
- satisfying existing tests or MCP expectations

The important change is that computation and rendering stop depending on raw nested dict contracts internally.

## Import And Parser Typing Design

Refactor `minx_mcp/finance/importers.py` and parser modules so parsed finance data travels through typed internal models.

### Parsed import model

Introduce a parsed batch model with fields for:

- `account_name`
- `source_type`
- `source_ref`
- `raw_fingerprint`
- `transactions`

Each parsed transaction should have typed fields for:

- `posted_at`
- `description`
- `merchant`
- `amount_cents`
- `category_hint`
- `external_id`

### Generic CSV mapping

If it simplifies code and validation, introduce a typed mapping model for generic CSV imports rather than repeatedly indexing `dict[str, object]`.

### Service integration

`FinanceService.finance_import()` should consume these typed parsed models directly and only convert at narrow seams if necessary.

## Testing Requirements

### Report lifecycle tests

Add or update tests proving:

- report rows move through `pending` -> `completed`
- report rows become `failed` when post-write DB work fails
- best-effort file cleanup is attempted on failure after write
- reruns repair prior `failed` and `pending` rows
- one logical report window has one authoritative DB row

### Vault writer tests

Add tests proving:

- markdown writes are atomic replacements
- invalid paths are still rejected
- overwrite behavior is preserved

### Typed model tests

Add tests proving:

- report builders return the expected typed internal objects
- parser/importer outputs are typed and validated
- serialization back to stored/service-facing dicts remains correct

### Existing behavior regression tests

Preserve coverage for:

- report content sections
- money formatting
- import idempotency
- event emission behavior
- daily review generation and fallback behavior

## Documentation

The publish surface should be minimal and practical.

Required docs:

- refresh `README.md` so it reflects actual setup, test, and runtime usage
- include one short "known limitations" section if any meaningful limitations remain after stabilization

Do not automatically publish all current handoff/spec/planning artifacts as part of the stable public story. Keep only docs that materially help someone use or understand the shipped project.

## Verification Before Publish

Before anything is fast-forwarded onto `main`, require fresh evidence for:

- full test suite passing
- expanded `mypy` coverage for the cleaned finance internals
- editable install from a clean checkout
- `minx-finance --transport stdio` startup smoke
- `minx-finance --transport http` startup smoke
- one end-to-end usage path:
  - import sample data
  - categorize
  - generate weekly or monthly report
  - generate daily review
  - confirm files, DB rows, and events line up

## Git And Publish Strategy

Implementation should continue on the current feature branch. Once the branch is verified and the repo is in a clean publishable state:

1. switch to `main`
2. fast-forward or merge the verified branch result
3. push `main`

This still satisfies the user's request to push to `main`, while avoiding risky in-progress development directly on `main`.

## Risks And Trade-Offs

### Changing `finance_report_runs` semantics

Moving from append-only history to one authoritative row per logical report window simplifies repair and usability, but it intentionally drops attempt-history fidelity. That is acceptable for this project stage.

### Moderate internal API churn

Typed cleanup across reports/importers/service may touch a meaningful number of files. The scope must stay bounded to finance internals and not expand into speculative cleanup elsewhere.

### Publish pressure

Because the target includes pushing to GitHub `main`, verification discipline matters more than cleanup ambition. If a refactor improves elegance but puts runtime confidence at risk, runtime confidence wins.
