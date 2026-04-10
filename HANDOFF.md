# Project Handoff

Status as of 2026-04-10: the Phase 1 hardening pass is implemented, the post-review follow-up fixes are also implemented, and the repo is in a green tested state on branch `codex/llm-finance-hardening-phase1`. Use this file as the canonical starting point for the next agent.

## Repo And Branch

- Repo: `/Users/akmini/Documents/minx-mcp`
- Branch: `codex/llm-finance-hardening-phase1`
- Stack: Python 3.12, FastMCP, SQLite, Pydantic, pytest, OpenAI-compatible LLM path

## Canonical Design Inputs

- Current domains hardening spec: [docs/superpowers/specs/2026-04-09-current-domains-hardening-and-finance-maturity-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-09-current-domains-hardening-and-finance-maturity-design.md)
- Current domains hardening plan: [docs/superpowers/plans/2026-04-09-current-domains-hardening-and-finance-maturity.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/plans/2026-04-09-current-domains-hardening-and-finance-maturity.md)
- Follow-up remediation notes for the completed post-review pass: [docs/superpowers/plans/2026-04-10-phase1-hardening-followups.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/plans/2026-04-10-phase1-hardening-followups.md)

## Current State

- Phase 1 hardening is complete.
- The later review pass and all confirmed findings from that review have been fixed.
- Full test suite is green.
- The worktree is not clean because the Phase 1 follow-up fixes and handoff updates are still uncommitted.

## What Phase 1 Hardened

Phase 1 tightened the Core and Finance interpretation layer at the real MCP boundary, not just in isolated helpers.

- `goal_capture` now wires the configured LLM through the actual Core MCP tool path instead of silently ignoring the LLM preference.
- Goal and finance interpretation helpers are async-safe and no longer use nested `asyncio.run()` inside running event loops.
- Finance query contracts were tightened:
  - reversed date ranges are rejected
  - blank filter text is rejected
  - clarify payloads are validated structurally
  - aggregate finance queries now write audit rows with `session_ref`
- Shared interpretation context builders now cap prompt lists and interpretation failures log compact summaries instead of raw prompt text.
- Goal-capture LLM prompts now include real goal context rather than `active_goals=[]`.

## What The Follow-Up Review Fixed

All concrete issues found in the deep review pass are now fixed.

- Interpretation validation failures no longer leak user content through log messages in [minx_mcp/core/interpretation/runner.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/runner.py).
- Goal-capture LLM updates are now supported for goal lifecycle actions, with prompt fields for `goal_id` and `update_kind`, in [minx_mcp/core/goal_capture.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/goal_capture.py) and [minx_mcp/core/interpretation/models.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/models.py).
- Conversational goal updates no longer consider expired active goals in [minx_mcp/core/server.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/server.py).
- Goal-capture prompts now label the mixed active/paused set as `Candidate goals` instead of `Active goals` in [minx_mcp/core/goal_capture.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/goal_capture.py).
- Sensitive finance queries now validate single-bound dates and reject blank scalar filters consistently in [minx_mcp/finance/server.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/server.py).
- `finance_query` list intents now audit under `finance_query` instead of `sensitive_finance_query` in [minx_mcp/finance/analytics.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py) and [minx_mcp/finance/service.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py).
- Audit logging no longer commits unrelated ambient transactions during read helpers in [minx_mcp/audit.py](/Users/akmini/Documents/minx-mcp/minx_mcp/audit.py).
- Import snapshot parsing now detects source kind from the immutable snapshot or temp file rather than the live source path in [minx_mcp/finance/importers.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/importers.py).
- Finance server LLM resolution now relies on an explicit `db_path` contract instead of the private `_db_path` attribute in [minx_mcp/finance/server.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/server.py) and [minx_mcp/finance/service.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py).

## Key Files Touched

- [minx_mcp/core/server.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/server.py)
- [minx_mcp/core/goal_capture.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/goal_capture.py)
- [minx_mcp/core/llm.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/llm.py)
- [minx_mcp/core/interpretation/models.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/models.py)
- [minx_mcp/core/interpretation/finance_query.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/finance_query.py)
- [minx_mcp/core/interpretation/runner.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/runner.py)
- [minx_mcp/core/interpretation/context.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/context.py)
- [minx_mcp/core/interpretation/logging.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/logging.py)
- [minx_mcp/finance/server.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/server.py)
- [minx_mcp/finance/analytics.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py)
- [minx_mcp/finance/service.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py)
- [minx_mcp/finance/importers.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/importers.py)
- [minx_mcp/audit.py](/Users/akmini/Documents/minx-mcp/minx_mcp/audit.py)

## Key Regression Coverage Added Or Expanded

- [tests/test_interpretation_runner.py](/Users/akmini/Documents/minx-mcp/tests/test_interpretation_runner.py)
- [tests/test_goal_capture.py](/Users/akmini/Documents/minx-mcp/tests/test_goal_capture.py)
- [tests/test_core_server.py](/Users/akmini/Documents/minx-mcp/tests/test_core_server.py)
- [tests/test_finance_server.py](/Users/akmini/Documents/minx-mcp/tests/test_finance_server.py)
- [tests/test_finance_service.py](/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py)
- [tests/test_finance_parsers.py](/Users/akmini/Documents/minx-mcp/tests/test_finance_parsers.py)
- [tests/test_finance_query_interpretation.py](/Users/akmini/Documents/minx-mcp/tests/test_finance_query_interpretation.py)

## Verification

Latest verification on this branch:

- `pytest -q tests/test_interpretation_runner.py tests/test_goal_capture.py tests/test_core_server.py`
  - `83 passed`
- `pytest -q tests/test_finance_server.py tests/test_finance_service.py tests/test_finance_parsers.py`
  - `81 passed`
- `pytest -q`
  - `399 passed in 2.17s`

## Remaining Non-Blocking Notes

- Budget suggestion or auto-budget generation is still intentionally deferred until after Phase 3. The dependency chain is still Phase 2 data quality, then Phase 3 goal-aware intelligence, then budget suggestions.
- Merchant normalization, staged rules, import preview, and richer monitoring remain future work and are the most natural inputs to Phase 2.
- `goal_capture` LLM update support now exists, but any future expansion should keep the deterministic ambiguity safeguards intact so ambiguous merchant/category matches still return `clarify`.

## Best Next Step

If continuing delivery work, start Phase 2 from the current domains hardening spec and plan, using the now-green Phase 1 branch as the baseline. If wrapping this branch first, the next action is to commit the current worktree and open the PR from `codex/llm-finance-hardening-phase1`.
