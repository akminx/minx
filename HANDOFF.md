# Project Handoff

Status as of 2026-04-09: Slice 2 is stable, and the repo also now contains the repo-scoped Core portion of Slice 2.1 (`goal_capture` plus the protected `daily_review` boundary). Harness-specific instance setup is intentionally deferred to later slices. Hardening pass complete — all correctness/cleanup gaps from the prior baseline are now closed. The next move is Slice 3. Use this file as the canonical starting point for the next agent.

## Current State

- Slice 1: Implemented
- Slice 2: Implemented for the repo-contained Core scope
- Slice 2.1: Repo-scoped Core work implemented (`goal_capture`, protected `daily_review` projection, stdio coverage); harness-specific instance setup remains intentionally deferred

The repo currently has local, uncommitted doc/workspace changes. Do not assume a clean worktree.

## What Was Fixed In The Hardening Pass (2026-04-09 follow-up)

- Goal filter validation: blank/whitespace-only `category_names`, `merchant_names`, and `account_names` members are now rejected with a stable `InvalidInputError`; valid members are trimmed before persistence.
- Protected review attention semantics: `goal_attention_level` now reflects only goals with `off_track` or `watch` status, not mere presence of active goals; `spending` is only added to `attention_areas` when `uncategorized_total_cents > 0`, not on any day with normal spending.
- Wheel packaging test: `test_built_wheel_includes_packaged_migrations` now uses `setuptools.build_meta.build_wheel` directly instead of `pip wheel`; passes reliably in the uv-managed environment.
- `assert payload is not None` guards in `goal_capture.py` replaced with explicit `RuntimeError` to survive `-O` optimization.
- `OSError` swallowed silently in `import_workflow._canonicalize_existing_path` now emits a `logging.warning`.
- Test count moved from 334 passed / 1 failed to 344 passed / 0 failed.

## What Was Fixed In The Prior Pass

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
- `daily_review` now returns a protected client-facing projection with explicit redaction metadata at the MCP boundary rather than the raw internal artifact.
- Non-`normal` events are filtered out of the review timeline/output path as the current repo-level sensitivity policy.
- The OpenAI-compatible provider path validates malformed provider envelopes before reading `choices[0].message.content`.
- The Core MCP server has an async `daily_review` tool, a transport-agnostic `goal_capture` tool, and a real stdio smoke test covering initialize, tool listing, goal capture/create/get/update, and protected review output.

## Current MCP Workflow Status

- Core MCP tools available: `daily_review`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, `goal_archive`
- Core MCP tools available: `daily_review`, `goal_capture`, `goal_create`, `goal_list`, `goal_get`, `goal_update`, `goal_archive`
- Runtime smoke test: [tests/test_core_mcp_stdio.py](/Users/akmini/Documents/minx-mcp/tests/test_core_mcp_stdio.py)
- LLM provider path: optional `core/llm_config` with `provider: "openai_compatible"`
- Expected server entrypoint: [minx_mcp/core/__main__.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/__main__.py)
- `goal_create` callers may omit `domain`; the MCP boundary defaults it to `"finance"`
- `goal_list()` defaults to active goals; `goal_list(status=...)` enforces the same status validation contract as other goal-tool status surfaces
- `goal_capture` returns explicit `create` / `update` / `clarify` / `no_match` proposals and does not mutate by itself

## Slice 2 Completion Notes

Slice 2 repo-scope acceptance criteria are satisfied, and the repo also implements the Core-side portion of Slice 2.1:

- `goal_list` rejects invalid `status` values with a stable `INVALID_INPUT` contract response
- `goal_list()` defaults to active goals for harness-friendly reads
- the `goal_create(domain)` boundary decision is implemented and tested with a default of `"finance"`
- `goal_type` is validated before persistence
- the missing regression tests for empty `review_date`, omitted `starts_on`, and review-date-clamped progress now exist and pass
- `GoalProgress` has coverage for above-target goal modes and the `"met"` path
- `goal_capture` exists and is covered through unit, server, stdio, and repo-level e2e tests
- `daily_review` returns a protected projection with redaction metadata at the MCP boundary
- category drift covers category-, merchant-, and account-scoped goals
- review output excludes non-`normal` events as the current sensitivity policy
- `uv run python -m pytest tests -q` currently yields `334 passed, 1 failed`; the remaining failure is the wheel-packaging test environment path described below
- `.venv/bin/python -m mypy` is green

The next agent should not treat Slice 2.1 as untouched, and should not start from a blank "implement conversational goals/trust hardening" assumption. The higher-value next move is to finish the cleanup/hardening gaps in the current baseline and then move to Slice 3 rather than replanning Core-side 2.1 work.

## Recommended Next Slice

From the current repo baseline, the recommended next implementation slice is now:

- **Slice 3: Meals MCP**

All hardening follow-ups from the prior baseline are now complete. The repo is clean and verified at 344 tests / 0 failures.

Harness-specific instance setup and client orchestration remain intentionally deferred until later harness-adaptation work.

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

- Goal filter normalization: reject empty/whitespace-only category, merchant, or account filters at the service layer.
- Protected review semantics: decide whether "attention" means pressure/needs-review or simple presence, then align naming/tests accordingly.
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
- Command: `uv run python -m pytest tests -q`
- Result: `344 passed in 1.90s`
- Command: `uv run python -m mypy`
- Result: `Success: no issues found in 50 source files`

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
- Hermes/Discord or Discord-specific client wiring is still out of scope for this repo.
- Sensitivity handling now includes a protected `daily_review` projection, but it is still a coarse deterministic policy rather than a generalized trust engine.
- Insight expiration and read-model snapshot persistence remain deferred; this repo currently persists detector insights without expiration filtering or snapshot storage.
- Slice 2 remains local-first and single-user; there is no auth or remote durability model.
- Daily review and report durability are still best-effort across SQLite and vault writes rather than globally atomic.

## Spec Notes And Deviations

- The roadmap/design text described `detect_category_drift` as a 4-week rolling-average comparison. The shipped Slice 2 cleanup uses the smaller equal-length immediately preceding baseline instead.
- `goal_get` is still the only goal tool that exposes derived progress; the other goal tool response shapes remain goal-only/list-only.
- The repo has already implemented the Core-side portion of Slice 2.1 (`goal_capture` and the protected `daily_review` boundary), even though earlier roadmap wording described that slice as future work.
- The roadmap has now been refreshed so the remaining deferred items live in explicit later work (`Slice 3+` domain expansion, later harness-adaptation slices, and `Slice 6`) instead of being implied as shipped Slice 2 work.

## Best Next Starting Points

- For Slice 3 planning, start with [docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md), [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md), and the existing Finance domain patterns under [minx_mcp/finance](/Users/akmini/Documents/minx-mcp/minx_mcp/finance).
- For later harness-specific adaptation work, start with [docs/superpowers/specs/2026-04-08-slice2-1-conversational-goals-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-08-slice2-1-conversational-goals-design.md), [docs/superpowers/specs/2026-04-08-slice2-1-trust-hardening-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-08-slice2-1-trust-hardening-design.md), and [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md).
- Before making changes, rerun:
  - `.venv/bin/python -m pytest tests/test_core_server.py tests/test_goal_progress.py tests/test_goals.py tests/test_detectors.py tests/test_llm.py tests/test_core_mcp_stdio.py -q`
  - `.venv/bin/python -m pytest tests -q`
  - `.venv/bin/python -m mypy`
