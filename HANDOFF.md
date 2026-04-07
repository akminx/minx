# Project Handoff

Status as of 2026-04-07: the repository is in a publishable, verified state on `main`, and that branch has been pushed to `origin` at commit `831bee5` (`chore: stabilize project for publish`).

## Current State

- Local checkout: clean on `main`
- Remote: `origin` -> `https://github.com/akminx/minx.git`
- Pushed commit: `831bee5c092428defbbf3d18ae67e39412f1b7ae`
- Preserved local-only docs backup branch: `codex/local-docs-backup`

## What Shipped

Core finance/report stabilization:
- `minx_mcp/finance/service.py`
- `minx_mcp/finance/reports.py`
- `minx_mcp/schema/migrations/006_finance_report_lifecycle.sql`
- `schema/migrations/006_finance_report_lifecycle.sql`

Typed internal finance models:
- `minx_mcp/finance/import_models.py`
- `minx_mcp/finance/report_models.py`
- `minx_mcp/finance/importers.py`
- `minx_mcp/finance/dedupe.py`
- parser modules under `minx_mcp/finance/parsers`

Runtime and filesystem hardening:
- `minx_mcp/vault_writer.py`
- `minx_mcp/transport.py`
- `minx_mcp/jobs.py`

Documentation and verification config:
- `README.md`
- `pyproject.toml`
- `HANDOFF.md`

Regression coverage:
- `tests/test_db.py`
- `tests/test_finance_parsers.py`
- `tests/test_finance_reports.py`
- `tests/test_report_lifecycle.py`
- `tests/test_transport.py`
- `tests/test_vault_writer.py`

## Important Behavior In Place

- Report generation now has explicit lifecycle state in SQLite: `pending`, `completed`, and `failed`.
- Report runs are deduplicated by `(report_kind, period_start, period_end)` instead of allowing ambiguous duplicate bookkeeping rows.
- If report generation fails after the markdown artifact is written but before durable DB completion, the run is marked failed and the artifact is cleaned up on a best-effort basis.
- Vault markdown writes are now atomic via temp-file-plus-rename.
- Finance parser and importer internals use typed models instead of broad `dict[str, object]` payloads.
- Weekly and monthly report building now uses typed summary models internally and only converts to dicts at response/storage boundaries.
- HTTP transport shutdown treats `KeyboardInterrupt` as a clean exit path.
- Job submission now raises clearly if a just-created job cannot be reloaded, instead of assuming that invariant silently holds.

## Verification

Latest verification on the shipped state:
- command: `.venv/bin/python -m pytest tests/ -q`
- result: `182 passed`
- command: `.venv/bin/python -m mypy`
- result: `Success: no issues found in 9 source files`
- command: `.venv/bin/python -m pip install -e '.[dev]'`
- result: passed
- command: finance runtime startup smoke checks over stdio and HTTP
- result: both startup modes worked, and HTTP shutdown via Ctrl-C exited cleanly

## Operational Notes

- The README now documents setup, env overrides, startup commands, and the verified local workflow.
- The primary checkout is intentionally clean on `main`.
- Older local planning/docs work that was not meant for publish was preserved on `codex/local-docs-backup` rather than mixed into `main`.
- The GitHub PAT used for push was pasted into this thread, so it should be revoked and replaced after confirming the remote state is correct.

## Remaining Caveats

- Normal finance imports now stream into a hashed snapshot before parse instead of fully buffering the source file in memory.
- Import callers and automations still need to stage files under the configured staging/import root.
- Live runtime smoke checks were run manually; they are not yet automated as a dedicated end-to-end test.
- Parser deduplication/shared abstractions across multiple CSV sources can still be improved later if more source formats are added.

## Suggested Next Step

Use the pushed `main` branch as the new baseline. The next highest-value cleanup is deeper simplification of finance parsing/import ergonomics only if new real usage exposes friction; the project is already in a stable enough state to use now.
