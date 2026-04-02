# Minx Core Platform + Finance Domain Design

**Date:** 2026-04-01
**Status:** Approved for planning
**Scope:** First spec in a multi-spec Minx rebuild

## Goal

Design the first implementation slice of `minx-mcp`: a portable shared platform plus a complete finance domain that supports imports, reusable CSV mappings, manual and rule-based categorization, anomaly detection, sensitive querying, weekly and monthly summaries, vault report output, and both `stdio` and HTTP-ready transport.

## Why This Slice First

The original rebuild plan combines several distinct subsystems:

- shared platform foundations
- finance MCP domain
- health MCP domain
- meals MCP domain
- dashboard MCP and HTTP UI
- vault restructuring and templates
- Hermes cutover

This spec intentionally narrows the first slice to shared core plus finance. That gives the project one full vertical path with real value while keeping future specs free to build on stable platform contracts instead of reworking them.

## Success Criteria

This spec is successful when:

- the repo has a portable shared core with clear runtime and storage boundaries
- finance imports support `DCU` CSV/PDF, `Discover` PDF, `Robinhood Gold` CSV, and reusable mapped generic CSV imports
- duplicate imports are idempotent and job-backed
- finance categorization supports both manual assignment and reusable rule-based auto-categorization
- finance exposes safe and sensitive MCP tools with audit logging on sensitive access
- finance can generate weekly and monthly reports and summaries into the Obsidian-compatible vault
- the same server surface can run via `stdio` and an HTTP transport shape that future dashboard work can depend on

## Non-Goals

This spec does not include:

- the dashboard frontend or dashboard-specific HTTP app implementation
- health tracking features
- meals and pantry features
- vault-wide restructuring or migration tooling
- Hermes configuration cutover work
- a dynamic plugin marketplace for third-party importers

## Product Decisions

The following product decisions are locked in for this spec:

- Finance is the first domain implemented.
- Day-one finance scope includes import, summary, categorization, anomaly detection, sensitive detail querying, and weekly/monthly reporting.
- Generic CSV support is reusable and saved per account or institution, not ad hoc per import.
- Categorization supports both direct manual assignment and stored rules.
- Reports and summaries are written to the vault in addition to being returned through MCP responses.
- The core platform must support `stdio` and an HTTP-ready transport boundary in the first slice.
- LiteParse is used behind a document text extraction adapter for PDF imports.
- Obsidian compatibility matters for vault outputs, but the Obsidian skills repo is not a runtime dependency of `minx-mcp`.

## Architecture Overview

The first slice is a monorepo with two architectural layers:

1. Shared core platform modules
2. A finance domain package built entirely on top of those shared modules

The shared core owns configuration, SQLite setup, migrations, jobs, audit logging, preferences, vault-safe markdown output, and transport bootstrapping. The finance domain owns import detection, parsing, normalization, dedupe, categorization, reporting, anomaly logic, and MCP tool registration.

Business logic must be transport-agnostic. The transport layer adapts requests from `stdio` or HTTP into the same service calls and enforces the same safe-vs-sensitive boundaries. Finance services should never contain transport-specific code.

The future dashboard should be able to depend on this slice without forcing a redesign. That means the finance domain needs stable data contracts, stable service contracts, and an HTTP-capable runtime boundary even though the dashboard itself is intentionally deferred to a later spec.

## Shared Core Platform

### Module Responsibilities

The shared core should be made of narrow, single-purpose modules:

- `config.py`
  - Resolves environment-driven paths and runtime settings.
  - Provides shared defaults for DB path, vault path, staging path, LiteParse binary, host, and port.

- `db.py`
  - Opens SQLite connections.
  - Applies idempotent migrations.
  - Enables required pragmas like WAL and foreign keys.
  - Centralizes row factory and connection lifecycle behavior.

- `jobs.py`
  - Creates and tracks long-running or retryable work such as imports and report generation.
  - Supports idempotency keys so retried requests reuse prior work when appropriate.
  - Stores status transitions and result references.

- `preferences.py`
  - Stores durable configuration and user-facing settings.
  - Persists reusable generic CSV mappings and report preferences.

- `audit.py`
  - Records access to sensitive finance data.
  - Captures tool name, session reference when available, and a short description of accessed data.

- `vault_writer.py`
  - Is the only approved path for writing markdown into the vault.
  - Enforces an allow-list of writable locations.
  - Supports full writes and named-section replacement for repeatable reports.

- `transport.py`
  - Runs MCP servers over `stdio` and an HTTP-ready shape.
  - Keeps per-domain `__main__` entrypoints tiny and uniform.

### Core Runtime Principles

- Shared modules should be domain-neutral and reusable by future health, meals, and dashboard specs.
- Core services must fail closed when a path, request, or permission boundary is unclear.
- Jobs and writes must be idempotent wherever practical because imports and reports may be retried by automations.
- Human-facing markdown generation belongs behind the vault writer boundary, not inside finance parser or query code.

## Data Model

### Platform Tables

The shared platform should own the following base tables:

- `_migrations`
- `jobs`
- `job_events`
- `preferences`
- `audit_log`

These tables support migration tracking, asynchronous or retried work, saved preferences, reusable importer mappings, and sensitive-access observability.

### Finance Tables

The finance domain should own:

- `finance_accounts`
- `finance_categories`
- `finance_category_rules`
- `finance_import_batches`
- `finance_transactions`
- `finance_transaction_dedupe`
- `finance_report_runs`

This structure separates:

- durable account metadata
- transaction-level facts
- reusable categorization heuristics
- import provenance
- duplicate protection
- report generation history

### Saved Generic CSV Mappings

Reusable generic CSV mappings should be stored as durable configuration keyed by account, institution, or named importer profile. A mapping should define:

- source profile name
- target account
- date column and parse format
- amount column or debit/credit column pair
- description and merchant columns when available
- optional category hint column
- optional skip rules for headers or footer rows

This gives the project the practical benefits of a general importer without requiring a heavyweight dynamic plugin system in v1.

## Importer Architecture

### Source Support

The first slice must support these inputs:

- `DCU` CSV
- `DCU` PDF
- `Discover` PDF
- `Robinhood Gold` CSV
- generic CSV via saved reusable mappings

### Importer Boundary

Each importer should implement the same normalized contract so the finance service never needs parser-specific branching after normalization:

```python
{
    "account_name": "Discover",
    "source_type": "pdf",
    "source_ref": "/path/to/file.pdf",
    "raw_fingerprint": "sha256:7d8e4c5a1f0b9d23",
    "transactions": [
        {
            "posted_at": "2026-03-28",
            "description": "HEB",
            "amount": -42.16,
            "merchant": "HEB",
            "category_hint": "groceries",
            "external_id": "optional-stable-id",
        }
    ],
}
```

This normalized contract is the seam between parsing and finance business logic.

### LiteParse Usage

LiteParse should be used through a small adapter boundary such as `extract_text(path: Path) -> str`. The finance domain should depend on that adapter instead of on LiteParse-specific APIs. This keeps PDF ingestion portable and makes testing parser behavior easier.

## Finance Domain Behavior

### Import Flow

`finance_import` should be a job-backed workflow:

1. Accept a path or file reference plus optional source hint.
2. Compute or reuse an idempotency key.
3. Create or reuse a job.
4. Detect the source type.
5. Parse and normalize the input.
6. Compute dedupe fingerprints.
7. Insert an import batch.
8. Insert only new transactions.
9. Apply category rules.
10. Update account import metadata.
11. Mark the job complete with a structured result summary.

If the same input is retried, the service should return the prior job or batch result instead of duplicating rows.

### Categorization

Categorization must support both:

- manual assignment to one or more selected transactions
- rule-based auto-categorization using merchant or pattern matching

Manual categorization updates the current state of a transaction. Rule-based categorization stores reusable heuristics that may be applied during import and re-applied to existing uncategorized rows. These should remain separate concepts so the system can explain whether a category came from direct user action or a stored rule.

### Anomaly Detection

Finance should expose anomaly detection in the first slice. The initial implementation can stay modest and deterministic:

- unusual transaction amount relative to merchant history
- unusual category totals relative to prior weekly or monthly windows
- new recurring-looking charges
- unusually large uncategorized transactions

The goal is useful review prompts, not a complex machine learning system.

### Safe And Sensitive Tool Boundaries

Finance MCP tools should be split clearly by data sensitivity.

Safe tools include:

- account listing
- aggregate summaries
- category rollups
- trends
- anomaly summaries
- job status
- report generation status or summaries

Sensitive tools include:

- raw transaction querying
- merchant-level detail views beyond standard summaries
- any response that exposes detailed personal transaction history

Sensitive tool use must create an `audit_log` entry with a concise summary of what was accessed.

## Reporting And Vault Output

Reports are first-class outputs in this slice. The finance domain should generate both structured summary data and markdown outputs written through `vault_writer.py`.

### Weekly Reports

Weekly reports should include:

- total outflow and inflow
- top categories
- notable merchants
- category changes versus the prior week
- anomalies flagged during the week
- uncategorized transactions needing review

### Monthly Reports

Monthly reports should include:

- account-level rollups
- category totals
- changes versus the prior month
- recurring charge highlights
- anomalies
- uncategorized or newly seen merchants

### Vault Boundary

Finance code should never write vault markdown directly. It should provide structured report data plus template inputs. The vault writer should own:

- path allow-list enforcement
- file creation and replacement behavior
- section replacement for repeatable summary files

This keeps report generation portable and safe while still fitting an Obsidian-compatible workflow.

## Transport Design

The first slice must support:

- `stdio` for local MCP clients like Hermes
- an HTTP-ready transport boundary for future dashboard use

The same finance service and policy layer must sit behind both transports. HTTP support in this spec is about establishing a shared runtime contract, not about implementing the dashboard UI or dashboard-specific HTTP app.

Each domain entrypoint should expose the same startup interface:

```text
--transport stdio|http
--host 127.0.0.1
--port 0
```

## Error Handling

Imports and report generation should be modeled as recoverable workflows rather than one-shot scripts.

- Parser errors should fail the job cleanly with a user-safe summary.
- Database writes for a single import should be transactional.
- Partial imports must not leave half-inserted rows behind.
- Sensitive-access denials should be explicit.
- Vault writes should fail closed when the target path is not approved or required data is missing.
- HTTP readiness and health should be exposed in a way future dashboard work can depend on.

## Testing Strategy

### Core Tests

The first slice should include tests for:

- migration idempotency
- job lifecycle and idempotency
- audit log writes for sensitive access
- preference persistence
- reusable CSV mapping persistence
- vault writer path enforcement
- named-section replacement behavior
- shared transport bootstrapping

### Finance Tests

The finance domain should include tests for:

- source detection
- `DCU` CSV parsing
- `DCU` PDF parsing
- `Discover` PDF parsing through the LiteParse adapter
- `Robinhood Gold` CSV parsing
- generic CSV mapping-driven import
- dedupe behavior on repeated imports
- manual categorization
- rule-based categorization
- anomaly detection
- safe summary responses
- sensitive query audit logging
- weekly report generation
- monthly report generation

### End-To-End Tests

A smaller set of end-to-end tests should verify:

- import to summary flow
- import to report output flow
- service behavior through `stdio`
- service behavior through the HTTP-capable transport boundary

## Future Specs Enabled By This Design

This spec intentionally sets up later work without absorbing it:

- dashboard spec can build on the HTTP-ready transport boundary and finance read models
- health spec can reuse jobs, preferences, audit, and vault writing
- meals spec can reuse the same platform foundations without inheriting finance-specific assumptions
- Hermes cutover spec can target stable tool names and runtime contracts

## Open Constraints To Carry Into Planning

The implementation plan for this spec should preserve these constraints:

- keep the first slice focused on shared core plus finance only
- avoid coupling runtime behavior to the Obsidian skills repo
- keep LiteParse behind an adapter seam
- treat reusable generic CSV mappings as saved configuration, not ad hoc import arguments
- keep transport concerns out of finance business logic
- keep human-facing markdown writes behind the vault writer boundary
