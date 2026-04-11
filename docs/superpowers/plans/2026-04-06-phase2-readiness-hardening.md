**Status: Completed (historical).** This plan was executed in an earlier slice. The codebase has since evolved — see [HANDOFF.md](/Users/akmini/Documents/minx-mcp/HANDOFF.md) for current state.

# Phase 2 Readiness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten finance MCP boundaries, add a real type-check gate, scope category-rule reapplication for imports, and harden vault section replacement before phase 2.

**Architecture:** Keep the current finance architecture intact and make surgical changes at the existing seams. Boundary cleanup stays in finance read-path code, type tightening stays at the MCP boundary and shared helpers, import performance hardening stays in `FinanceService`, and markdown-section robustness stays in `VaultWriter`.

**Tech Stack:** Python 3.12, pytest, mypy, FastMCP, SQLite

---

### Task 1: Tighten Sensitive Query Boundary

**Files:**
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py`
- Modify: `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py`

- [ ] Add a failing test that asserts `sensitive_finance_query()` returns `amount` but not `amount_cents`.
- [ ] Run the targeted test and confirm it fails for the current leaked boundary.
- [ ] Update `sensitive_query()` to build explicit response rows instead of splatting raw SQLite rows.
- [ ] Re-run the targeted test and confirm it passes.

### Task 2: Add Typed MCP Boundary And Enforced Type Check

**Files:**
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/server.py`
- Modify: `/Users/akmini/Documents/minx-mcp/pyproject.toml`
- Modify: `/Users/akmini/Documents/minx-mcp/README.md`
- Modify: `/Users/akmini/Documents/minx-mcp/HANDOFF.md`

- [ ] Add `mypy` to the dev toolchain and configure a small initial type-check target that is expected to stay green.
- [ ] Replace `object`-typed finance server service parameters with a typed interface or concrete service type.
- [ ] Run `mypy` on the configured target and fix any issues until it passes cleanly.
- [ ] Document the `mypy` command alongside the existing test verification notes.

### Task 3: Scope Rule Reapplication During Import

**Files:**
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py`
- Modify: `/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py`

- [ ] Add a failing test that proves batch-scoped rule application only updates rows from the imported batch when `batch_id` is supplied.
- [ ] Run the targeted test and confirm it fails against the current global-update implementation.
- [ ] Extend `apply_category_rules()` with an optional batch scope while preserving the existing full-table manual invocation behavior.
- [ ] Re-run the targeted test and the existing categorization tests to confirm both scoped and unscoped behavior work.

### Task 4: Harden Vault Section Replacement

**Files:**
- Modify: `/Users/akmini/Documents/minx-mcp/minx_mcp/vault_writer.py`
- Modify: `/Users/akmini/Documents/minx-mcp/tests/test_vault_writer.py`

- [ ] Add a failing test showing that a matching heading string inside a fenced code block must not be treated as a real section boundary.
- [ ] Run the targeted test and confirm it fails against the current raw-string split approach.
- [ ] Replace the split-based implementation with a line-oriented parser that matches level-2 headings outside fenced code blocks.
- [ ] Re-run the vault writer tests and confirm the new parser still preserves the existing success cases.

### Task 5: Final Verification And Phase 2 Notes

**Files:**
- Modify: `/Users/akmini/Documents/minx-mcp/HANDOFF.md`

- [ ] Run the full test suite: `.venv/bin/python -m pytest -q`
- [ ] Run the type check gate: `.venv/bin/python -m mypy minx_mcp/finance/server.py minx_mcp/finance/analytics.py minx_mcp/vault_writer.py`
- [ ] Update handoff notes with the completed hardening work and capture deferred follow-ups:
- [ ] Parser deduplication between CSV parsers
- [ ] Potential `_canonicalize_existing_path()` scaling work if staging directories grow
- [ ] Live HTTP smoke test automation instead of manual-only verification

## Deferred Follow-Ups For Phase 2

- Parser deduplication between `dcu.py` and `robinhood_gold.py` is intentionally deferred until another CSV source or a real shared edge case justifies the abstraction.
- `_canonicalize_existing_path()` should be revisited only if the staging directory grows enough for per-segment directory scans to matter.
- Live HTTP startup should become an automated smoke test once the next phase adds more runtime surface area.
