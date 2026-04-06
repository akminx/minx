# Finance Slice Handoff

Status as of 2026-04-06: the finance slice is in a solid stabilization state after the contracts pass, cents migration, and a final hardening pass across imports, jobs, report rendering, typed MCP boundaries, and vault section replacement. The latest verification run was `.venv/bin/python -m pytest -q`, which passed with `94 passed in 1.18s`.

## What's Built

Core config and runtime scaffolding:
- `minx_mcp/config.py`
- `minx_mcp/db.py`
- packaged migrations under `schema/migrations` and `minx_mcp/schema/migrations`

Shared platform helpers:
- `minx_mcp/jobs.py`
- `minx_mcp/preferences.py`
- `minx_mcp/audit.py`
- `minx_mcp/vault_writer.py`
- `minx_mcp/document_text.py`
- `minx_mcp/transport.py`

Finance ingest and parsing:
- `minx_mcp/finance/importers.py`
- parser modules under `minx_mcp/finance/parsers`

Finance domain logic:
- `minx_mcp/finance/service.py`
- `minx_mcp/finance/dedupe.py`
- `minx_mcp/finance/analytics.py`
- `minx_mcp/finance/reports.py`

MCP and CLI surface:
- `minx_mcp/finance/server.py`
- `minx_mcp/finance/__main__.py`

Report templates:
- `templates/finance-weekly-summary.md`
- `templates/finance-monthly-summary.md`

## Important Behavior In Place

- Import idempotency is content-hash based, so changed files at the same path create new jobs while duplicate transactions are still deduped.
- Stuck running jobs are auto-recovered and their idempotency key is released so retries can proceed.
- Failed jobs also release their idempotency key so callers can retry the same import cleanly.
- Rule reapplication does not overwrite manual categorization.
- `LIKE` wildcards in merchant rules are treated literally.
- Raw import fingerprints store a real content hash.
- Monthly summaries no longer return the duplicate `accounts` alias.
- Recurring-charge report SQL is deduplicated behind a helper.
- Thread-local DB connections are closed after MCP tool calls.
- MCP tool inputs are validated for paths, limits, ids, categories, patterns, and date formats/windows.
- Weekly and monthly report tools enforce actual weekly and full-calendar-month windows.
- Generic CSV imports can resolve saved mappings by account import profile, with account-name fallback.
- Sensitive query limits are enforced both at the MCP boundary and in the direct service API.
- Sensitive finance query responses expose dollar-facing `amount` values without leaking the internal `amount_cents` storage field.
- Imports are restricted to the configured staging/import root instead of arbitrary readable local files.
- `finance_categorize` reports the actual rows updated, not the raw input length.
- Import parsing now uses the hashed file snapshot, so stored fingerprints and parsed contents stay aligned even if the source file changes during import.
- Import idempotency now normalizes path aliases, including `..` aliases and case-only aliases on case-insensitive filesystems.
- Duplicate callers now return the in-flight `running` job instead of re-executing the same import body.
- Job submission now handles idempotency-key insert races without surfacing a unique-constraint error.
- Generic CSV imports now preserve the source sign instead of forcing all rows negative.
- Generated markdown reports now render money as currency strings (for example `$1200.00`, `-$45.20`).
- Import-triggered rule application is scoped to the newly imported batch instead of scanning the full transaction table for every import.
- Vault section replacement now matches `##` headings outside fenced code blocks instead of using raw string splitting.
- A targeted `mypy` gate now checks the finance MCP boundary and shared vault writer seams.

## Runtime Expectations

- The finance runtime constructs `FinanceService` with `settings.staging_path` as the allowed import root in `minx_mcp/finance/__main__.py`.
- If you use `FinanceService` directly in other scripts, pass an explicit `import_root` if you want something other than `db_path.parent`.
- The allowed import root is strict, so imports must be staged under that directory.

## Test Coverage

Coverage now includes:
- core DB, jobs, preferences, audit, vault, document, and transport checks
- finance parsers, service flows, reports, MCP server behaviors, and end-to-end paths
- hardening regressions around stuck-job recovery, failed-job retryability, path allowlisting, generic CSV mapping lookup, generic CSV sign preservation, literal `$` in template content, direct service validation, weekly/monthly window enforcement, accurate rowcount reporting, import path alias normalization, snapshot-consistent parsing, duplicate in-flight import handling, idempotency-key race recovery, sensitive-query boundary cleanup, batch-scoped rule application, and fenced-code-safe section replacement

Latest reported verification:
- command: `.venv/bin/python -m pytest -q`
- result: `94 passed in 1.18s`
- command: `.venv/bin/python -m mypy minx_mcp/finance/server.py minx_mcp/finance/analytics.py minx_mcp/vault_writer.py`
- result: `Success: no issues found in 3 source files`
- command: finance runtime startup smoke checks over HTTP and stdio
- result: HTTP bound `127.0.0.1:8765` successfully and stdio stayed alive after startup

## Non-Blocking Caveats

- Large imports still read files fully into memory before parse.
- Any caller or automation that imports data must stage files under the configured import root.
- Manual live runtime smoke checks passed for both HTTP and stdio startup, but this still is not encoded as an automated test.
- Parser deduplication between CSV sources is still deferred until another source or shared parsing need creates real pressure.
- `_canonicalize_existing_path()` is still optimized for correctness and simplicity over large-directory performance.

## Next Phase Summary

The finance slice is ready for the next implementation phase. The current foundation now has:
- stable import idempotency across content changes, path aliases, case-only aliases, and concurrent idempotency-key races
- retryable failed imports without manual job cleanup
- strict import-root enforcement and tighter MCP input validation at the boundary
- typed MCP server/service seams with an enforced `mypy` verification command
- generic CSV sign preservation for both inflow and outflow rows
- exact cents-based storage and arithmetic with dollar-facing outputs at the boundary
- sensitive-query payloads that stay at the external API boundary instead of leaking storage fields
- report window enforcement for weekly and monthly summaries
- currency-formatted markdown output for generated reports
- import-time rule application that scales better by scoping work to the current batch
- safer vault section replacement for future multi-domain markdown writing
- connection cleanup after MCP tool calls
- regression coverage for the most important hardening fixes found during review

Carry these caveats into the next phase:
- large imports are still fully buffered in memory before parse
- import callers and automations must stage files under the configured staging/import root
- a full live HTTP transport smoke test is still worth doing once the next phase introduces more runtime surface area
- parser deduplication between CSV sources can wait until a shared parser shape naturally emerges
- `_canonicalize_existing_path()` only needs a performance revisit if staging directories become large

## Suggested Next Step

Create a commit for the current finance slice once you are satisfied with the handoff document and working tree contents, then start the next phase from this hardened baseline.
