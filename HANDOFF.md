# Project Handoff

Status as of 2026-04-08: Slice 2 cleanup plus pre-push review fixes are complete and re-verified, and the roadmap projection has been refreshed to match the larger Life OS vision. Slice 1 is stable; Slice 2 is ready as the current repo baseline with explicit repo-scope deferrals and a recommended next slice called out below. Use this file as the canonical starting point for the next agent.

## Current State

- Slice 1: Implemented and green
- Slice 2: Implemented for the repo-contained Core scope, cleaned up, and re-verified
- Slice 2.1+: Not started in code; roadmap/projection updated

The repo currently has local, uncommitted changes in the Slice 2 core/server/test surfaces plus roadmap/planning docs. Do not assume a clean worktree.

## What Was Fixed In The Latest Pass

- `goal_get` returns both the stored goal DTO and derived progress, with optional `review_date` support and `progress: null` outside the goal lifetime.
- Goal validation is enforced in the service layer for trimmed `goal_type`/title, positive `target_value`, ISO dates, and `ends_on >= starts_on`.
- Goal updates can intentionally clear nullable fields via `clear_ends_on` and `clear_notes`.
- Goal progress uses the natural period window intersected with the goal lifetime.
- Goal progress no longer counts future-dated transactions after the requested `review_date`; measurement is clamped to the review point while pacing/status math still uses the full effective window.
- Empty-string dates no longer bypass validation and silently default to today in the MCP boundary for `daily_review` and `goal_create(starts_on=...)`.
- `goal_list()` now defaults to active goals, and `goal_list(status=...)` rejects invalid values and empty strings with a stable `INVALID_INPUT` contract response instead of silently widening or emptying results.
- `goal_create(domain)` now defaults `domain` to `"finance"` at the MCP boundary, including through the stdio server path.
- Public MCP/server regressions now pin `goal_get(review_date="")`, omitted `starts_on`, and review-date-clamped progress through the actual goal tool boundary.
- `GoalProgress` coverage now includes `sum_above`, `count_above`, and a direct `"met"` path assertion.
- Category drift compares the current elapsed span against the immediately preceding equal-length baseline, works for category/merchant/account filters, and only fires when both ratio and absolute delta thresholds are met.
- `daily_review` now returns the structured artifact fields directly at the MCP boundary instead of only summary counters plus markdown.
- Non-`normal` events are filtered out of the review timeline/output path as the current repo-level sensitivity policy.
- The OpenAI-compatible provider path validates malformed provider envelopes before reading `choices[0].message.content`.
- The Core MCP server has an async `daily_review` tool and a real stdio smoke test covering initialize, tool listing, `goal_create`, `goal_get`, and `daily_review`.

## Current MCP Workflow Status

- Core MCP tools available: `daily_review`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, `goal_archive`
- Runtime smoke test: [tests/test_core_mcp_stdio.py](/Users/akmini/Documents/minx-mcp/tests/test_core_mcp_stdio.py)
- LLM provider path: optional `core/llm_config` with `provider: "openai_compatible"`
- Expected server entrypoint: [minx_mcp/core/__main__.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/__main__.py)
- `goal_create` callers may omit `domain`; the MCP boundary defaults it to `"finance"`
- `goal_list()` defaults to active goals; `goal_list(status=...)` enforces the same status validation contract as other goal-tool status surfaces

## Slice 2 Completion Notes

Slice 2 repo-scope acceptance criteria are now satisfied:

- `goal_list` rejects invalid `status` values with a stable `INVALID_INPUT` contract response
- `goal_list()` defaults to active goals for harness-friendly reads
- the `goal_create(domain)` boundary decision is implemented and tested with a default of `"finance"`
- `goal_type` is validated before persistence
- the missing regression tests for empty `review_date`, omitted `starts_on`, and review-date-clamped progress now exist and pass
- `GoalProgress` has coverage for above-target goal modes and the `"met"` path
- `daily_review` exposes the structured artifact fields at the MCP boundary
- category drift covers category-, merchant-, and account-scoped goals
- review output excludes non-`normal` events as the current sensitivity policy
- `.venv/bin/python -m pytest tests -q` is green
- `.venv/bin/python -m mypy` is green

The next agent should treat the repo-contained Slice 2 work as closed unless new review feedback or new product requirements arrive. Deferred roadmap items still exist and are listed below. New work should start from Slice 3 or from explicitly requested follow-up polish.

## Recommended Next Slice

The roadmap has been refreshed and the recommended next implementation slice is now:

- **Slice 2.1: Conversational Goals + Trust Hardening**

That slice exists to absorb the most important deferred work before broader domain expansion:

- Hermes/Discord conversational goal capture as a thin client over Core goal tools
- stronger sensitivity/redaction policy for review inputs and outputs
- explicit client-side use of the structured `daily_review` artifact
- end-to-end verification of conversational goal flows through the Core boundary

If the product priority shifts away from Hermes/Discord interaction and toward richer cross-domain insight first, the fallback next move is:

- **Slice 3: Meals MCP**

## Roadmap Refresh

The roadmap/spec projection was updated in this pass:

- Canonical roadmap spec: [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md)
- Companion projection/ordering plan: [docs/superpowers/plans/2026-04-08-roadmap-projection-refresh.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/plans/2026-04-08-roadmap-projection-refresh.md)

Key roadmap changes:

- the roadmap now follows four arcs: Foundation, Domain Expansion, Interaction + Trust, Intelligence + Autonomy
- a new `Slice 2.1` bridges current Core goals work to Hermes/Discord and trust-policy work
- insight expiration and snapshot persistence now live explicitly in `Slice 6`
- autonomy and dashboard work stay late, after trust and durable-memory layers

## Extra Things Worth Considering While You Are There

These are not all necessarily bugs, but they are the next most likely places for regressions or product ambiguity.

- Inclusive boundary dates: add explicit tests for `review_date == starts_on` and `review_date == ends_on`.
- Update precedence: verify `clear_ends_on=True` wins over a simultaneously supplied `ends_on`, and `clear_notes=True` wins over `notes`.
- Goal status/filter validation symmetry: make sure empty strings are treated as invalid consistently anywhere status-like fields are accepted.
- Tool-level invalid-input symmetry: if `daily_review("")` is invalid, make sure analogous goal-tool inputs fail the same way everywhere.
- Multi-filter semantics in finance reads: confirm whether category + merchant + account filters are intended to be combined with `AND`.
- Lifecycle coverage: add more end-to-end assertions for `paused`, `completed`, and `archived` goals.
- Cold-start and sparse-history cases: add more explicit detector and goal-pacing coverage for zero-baseline, newly started goals, and thin-history windows.
- MCP boundary behavior for missing required args: decide deliberately which errors should be contract-shaped versus framework-native.

## Verification

Latest results from the current working tree:

- Command: `.venv/bin/python -m pytest tests/test_core_server.py tests/test_goal_progress.py tests/test_goals.py tests/test_core_mcp_stdio.py -q`
- Result: `50 passed in 0.69s`
- Command: `.venv/bin/python -m pytest tests/test_core_server.py tests/test_goal_progress.py tests/test_goals.py tests/test_detectors.py tests/test_llm.py tests/test_core_mcp_stdio.py -q`
- Result: `81 passed in 0.81s`
- Command: `.venv/bin/python -m pytest tests -q`
- Result: `271 passed in 2.15s`
- Command: `.venv/bin/python -m mypy`
- Result: `Success: no issues found in 48 source files`

## Former Acceptance Criteria

These were the Slice 2 cleanup gate checks and are now complete:

- `goal_list` rejects invalid `status` values with a stable `INVALID_INPUT` contract response
- the `goal_create(domain)` boundary decision is implemented and tested
- the missing regression tests listed above exist and pass
- `GoalProgress` has coverage for above-target goal modes and the `met` path
- `.venv/bin/python -m pytest tests -q` is green
- `.venv/bin/python -m mypy` is green
- `HANDOFF.md` is updated again with the new baseline and latest verification output

## Intentional Non-Blocking Deferrals

- Hermes/Discord conversational goal capture is still out of scope for this repo and remains deferred until a later slice.
- Sensitivity handling is currently a simple review-path exclusion policy for non-`normal` events, not a generalized redaction framework.
- Insight expiration and read-model snapshot persistence remain deferred; this repo currently persists detector insights without expiration filtering or snapshot storage.
- Slice 2 remains local-first and single-user; there is no auth or remote durability model.
- Daily review and report durability are still best-effort across SQLite and vault writes rather than globally atomic.

## Spec Notes And Deviations

- The roadmap/design text described `detect_category_drift` as a 4-week rolling-average comparison. The shipped Slice 2 cleanup uses the smaller equal-length immediately preceding baseline instead.
- `goal_get` is still the only goal tool that exposes derived progress; the other goal tool response shapes remain goal-only/list-only.
- The roadmap has now been refreshed so those deferred items live in explicit later slices (`Slice 2.1` and `Slice 6`) instead of being implied as shipped Slice 2 work.

## Best Next Starting Points

- For Slice 2.1 planning, start with [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md), [docs/superpowers/plans/2026-04-08-roadmap-projection-refresh.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/plans/2026-04-08-roadmap-projection-refresh.md), [minx_mcp/core/server.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/server.py), [minx_mcp/core/goals.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/goals.py), and [minx_mcp/core/review.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/review.py).
- For Slice 3 planning instead, start with [docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md), [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md), and the existing Finance domain patterns under [minx_mcp/finance](/Users/akmini/Documents/minx-mcp/minx_mcp/finance).
- Before making changes, rerun:
  - `.venv/bin/python -m pytest tests/test_core_server.py tests/test_goal_progress.py tests/test_goals.py tests/test_detectors.py tests/test_llm.py tests/test_core_mcp_stdio.py -q`
  - `.venv/bin/python -m pytest tests -q`
  - `.venv/bin/python -m mypy`
