# Finance Slice Handoff

Status as of 2026-04-02: the first finance slice is in a strong handoff state and has had a follow-up hardening pass. The working tree is still uncommitted, and the latest verification run was `.venv/bin/python -m pytest -q`, which passed with `69 passed in 1.12s`.

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
- Imports are restricted to the configured staging/import root instead of arbitrary readable local files.
- `finance_categorize` reports the actual rows updated, not the raw input length.
- Import parsing now uses the hashed file snapshot, so stored fingerprints and parsed contents stay aligned even if the source file changes during import.
- Import idempotency now normalizes path aliases, including `..` aliases and case-only aliases on case-insensitive filesystems.
- Duplicate callers now return the in-flight `running` job instead of re-executing the same import body.
- Job submission now handles idempotency-key insert races without surfacing a unique-constraint error.

## Runtime Expectations

- The finance runtime constructs `FinanceService` with `settings.staging_path` as the allowed import root in `minx_mcp/finance/__main__.py`.
- If you use `FinanceService` directly in other scripts, pass an explicit `import_root` if you want something other than `db_path.parent`.
- The allowed import root is strict, so imports must be staged under that directory.

## Test Coverage

Coverage now includes:
- core DB, jobs, preferences, audit, vault, document, and transport checks
- finance parsers, service flows, reports, MCP server behaviors, and end-to-end paths
- hardening regressions around stuck-job recovery, path allowlisting, generic CSV mapping lookup, literal `$` in template content, direct service validation, weekly/monthly window enforcement, accurate rowcount reporting, import path alias normalization, snapshot-consistent parsing, duplicate in-flight import handling, and idempotency-key race recovery

Latest reported verification:
- command: `.venv/bin/python -m pytest -q`
- result: `69 passed in 1.12s`

## Current Working Tree

The working tree is not committed yet. Current uncommitted paths:
- `minx_mcp/finance/__main__.py`
- `minx_mcp/finance/importers.py`
- `minx_mcp/finance/parsers/dcu.py`
- `minx_mcp/finance/parsers/discover.py`
- `minx_mcp/finance/parsers/generic_csv.py`
- `minx_mcp/finance/parsers/robinhood_gold.py`
- `minx_mcp/finance/reports.py`
- `minx_mcp/finance/server.py`
- `minx_mcp/finance/service.py`
- `minx_mcp/jobs.py`
- `tests/test_finance_reports.py`
- `tests/test_finance_server.py`
- `tests/test_finance_service.py`
- `tests/test_jobs.py`
- `tests/test_finance_templates.py` (new)
- `HANDOFF.md` (new)

## Non-Blocking Caveats

- Large imports still read files fully into memory before parse.
- Any caller or automation that imports data must stage files under the configured import root.
- The hardening pass did not include a full live HTTP socket smoke test against a bound MCP server; transport coverage is still primarily unit and integration style.
- Nothing in the current working tree has been committed yet.

## Next Phase Summary

The finance slice is ready for the next implementation phase. The current foundation now has:
- stable import idempotency across content changes, path aliases, case-only aliases, and concurrent idempotency-key races
- strict import-root enforcement and tighter MCP input validation at the boundary
- report window enforcement for weekly and monthly summaries
- connection cleanup after MCP tool calls
- regression coverage for the most important hardening fixes found during review

Carry these caveats into the next phase:
- large imports are still fully buffered in memory before parse
- import callers and automations must stage files under the configured staging/import root
- a full live HTTP transport smoke test is still worth doing once the next phase introduces more runtime surface area

## Suggested Next Step

Create a commit for the current finance slice once you are satisfied with the handoff document and working tree contents, then start the next phase from this hardened baseline.
