# Project Handoff

Status as of 2026-04-09: Slice 2 and the repo-scoped Core portion of Slice 2.1 remain stable, and the repo now contains a broader Phase 1 pass of the approved LLM reliability + finance hardening work, including the first natural-language finance query path. Harness-specific instance setup is still intentionally deferred. The next highest-value move is to continue the approved reliability/hardening spec before jumping to Slice 3. Use this file as the canonical starting point for the next agent.

## Current State

- Slice 1: Implemented
- Slice 2: Implemented for the repo-contained Core scope
- Slice 2.1: Repo-scoped Core work implemented (`goal_capture`, protected `daily_review` projection, stdio coverage); harness-specific instance setup remains intentionally deferred
- LLM Reliability + Finance Hardening: Phase 1 partially implemented in the current worktree, with finance NL querying now added on top of the shared interpretation foundation

The repo worktree contains local uncommitted changes from the new reliability/hardening pass and should not be treated as clean.

## What Landed In The New Reliability + Finance Hardening Pass

This pass implemented the first concrete slice of the approved spec and plan:

- Approved spec: [docs/superpowers/specs/2026-04-09-llm-reliability-and-finance-hardening-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-09-llm-reliability-and-finance-hardening-design.md)
- Implementation plan: [docs/superpowers/plans/2026-04-09-llm-reliability-and-finance-hardening.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/plans/2026-04-09-llm-reliability-and-finance-hardening.md)

Implemented in this worktree:

- Shared interpretation foundation:
  - new package scaffold in `minx_mcp/core/interpretation/`
  - typed interpretation result model in [minx_mcp/core/interpretation/models.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/models.py)
  - reusable structured interpretation runner in [minx_mcp/core/interpretation/runner.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/runner.py)
- Existing LLM path now exposes a reusable JSON-prompt entrypoint in [minx_mcp/core/llm.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/llm.py)
- `goal_capture` now has an initial LLM-backed interpretation path with deterministic validation and fallback to the legacy path in [minx_mcp/core/goal_capture.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/goal_capture.py)
- Finance import detection is no longer filename-only:
  - staged detection helper in [minx_mcp/core/interpretation/import_detection.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/import_detection.py)
  - [minx_mcp/finance/importers.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/importers.py) now falls back to sampled file content / extracted PDF text
- Finance sensitive reads now support deterministic filters through service + MCP boundary:
  - `start_date`
  - `end_date`
  - `category_name`
  - `merchant`
  - `account_name`
  - `description_contains`
- Finance now has an initial natural-language query path:
  - new finance-query interpretation module in [minx_mcp/core/interpretation/finance_query.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation/finance_query.py)
  - typed finance query plan models in [minx_mcp/core/models.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/models.py)
  - new MCP-facing `finance_query` tool in [minx_mcp/finance/server.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/server.py)
  - deterministic execution for `list_transactions`, `sum_spending`, and `count_transactions` via [minx_mcp/finance/service.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py) and [minx_mcp/finance/analytics.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py)
- The OpenAI-compatible provider path now supports JSON-only interpretation prompts in [minx_mcp/core/llm_openai.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/llm_openai.py)
- `category_hint` is now wired on import insert with a deterministic best-effort category match in [minx_mcp/finance/service.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py)
- Finance anomaly threshold is now preference-backed instead of hardcoded via [minx_mcp/preferences.py](/Users/akmini/Documents/minx-mcp/minx_mcp/preferences.py) and [minx_mcp/finance/analytics.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py)
- New or expanded test coverage landed in:
  - [tests/test_interpretation_runner.py](/Users/akmini/Documents/minx-mcp/tests/test_interpretation_runner.py)
  - [tests/test_goal_capture.py](/Users/akmini/Documents/minx-mcp/tests/test_goal_capture.py)
  - [tests/test_finance_parsers.py](/Users/akmini/Documents/minx-mcp/tests/test_finance_parsers.py)
  - [tests/test_finance_query_interpretation.py](/Users/akmini/Documents/minx-mcp/tests/test_finance_query_interpretation.py)
  - [tests/test_finance_service.py](/Users/akmini/Documents/minx-mcp/tests/test_finance_service.py)
  - [tests/test_finance_server.py](/Users/akmini/Documents/minx-mcp/tests/test_finance_server.py)

## What Is Still Outstanding From The Approved Spec

The following approved Phase 1 / Phase 2 items have not been implemented yet:

- richer shared interpretation context builders beyond the current foundation
- staged finance rules inspired by Actual
- merchant normalization / aliasing
- import preview / dry-run
- audit summary surfacing
- interpretation observability/logging beyond the current minimal foundation
- event/review reproducibility groundwork

The next agent should continue from the approved spec/plan rather than replanning from scratch.

## What Was Fixed In The Hardening Pass (2026-04-09 follow-up)

- Goal filter validation: blank/whitespace-only `category_names`, `merchant_names`, and `account_names` members are now rejected with a stable `InvalidInputError`; valid members are trimmed before persistence.
- Protected review attention semantics: `goal_attention_level` now reflects only goals with `off_track` or `watch` status, not mere presence of active goals; `spending` is added to `attention_areas` for uncategorized spending or an actual `finance.spending_spike` signal, not for any day with ordinary spending.
- Wheel packaging test: `test_built_wheel_includes_packaged_migrations` now uses `setuptools.build_meta.build_wheel` directly instead of `pip wheel`; passes reliably in the uv-managed environment.
- `assert payload is not None` guards in `goal_capture.py` replaced with explicit `RuntimeError` to survive `-O` optimization.
- `OSError` swallowed silently in `import_workflow._canonicalize_existing_path` now emits a `logging.warning`.
- Test count moved from 334 passed / 1 failed to 346 passed / 0 failed after the follow-up review fixes in this working tree.

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
- `GoalProgress.summary` should be treated as human-facing copy rather than a strict machine contract; downstream code should use structured progress fields instead of parsing prose
- `uv run python -m pytest tests -q` is green
- `.venv/bin/python -m mypy` is green

The next agent should not treat Slice 2.1 as untouched, and should not start from a blank "implement conversational goals/trust hardening" assumption. The higher-value next move is to finish the cleanup/hardening gaps in the current baseline and then move to Slice 3 rather than replanning Core-side 2.1 work.

## Recommended Next Move

From the current repo baseline, the recommended next implementation move is now:

- continue Phase 1 of the approved LLM reliability + finance hardening plan

Concretely, the best next task is:

- strengthen the new finance interpretation path with richer shared context builders, better clarification synthesis, and observability/logging

Only after the approved reliability/hardening work is in a good place should the next agent shift focus to:

- **Slice 3: Meals MCP**

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

- Command: `uv run python -m pytest tests/test_finance_query_interpretation.py tests/test_finance_server.py -q`
- Result: `26 passed in 0.50s`
- Command: `uv run python -m pytest tests/test_llm.py tests/test_interpretation_runner.py tests/test_finance_service.py tests/test_finance_server.py tests/test_finance_query_interpretation.py -q`
- Result: `69 passed in 0.56s`
- Command: `uv run python -m pytest tests -q`
- Result: `362 passed in 2.02s`
- Command: `uv run python -m mypy`
- Result: `Success: no issues found in 55 source files`

- Command: `uv run python -m pytest tests/test_interpretation_runner.py tests/test_llm.py tests/test_goal_capture.py tests/test_core_server.py tests/test_core_mcp_stdio.py tests/test_finance_parsers.py tests/test_finance_service.py tests/test_finance_server.py -q`
- Result: `151 passed in 1.26s`
- Command: `uv run python -m pytest tests -q`
- Result: `359 passed in 2.24s`
- Command: `uv run python -m mypy`
- Result: `Success: no issues found in 54 source files`

- Command: `.venv/bin/python -m pytest tests/test_core_server.py tests/test_goal_progress.py tests/test_goals.py tests/test_core_mcp_stdio.py -q`
- Result: `50 passed in 0.69s`
- Command: `.venv/bin/python -m pytest tests/test_core_server.py tests/test_goal_progress.py tests/test_goals.py tests/test_detectors.py tests/test_llm.py tests/test_core_mcp_stdio.py -q`
- Result: `81 passed in 0.81s`
- Command: `uv run python -m pytest tests -q`
- Result: `346 passed in 1.98s`
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
- `GoalProgress.summary` remains intentionally human-facing helper text rather than a stability-pinned machine contract.
- The repo has already implemented the Core-side portion of Slice 2.1 (`goal_capture` and the protected `daily_review` boundary), even though earlier roadmap wording described that slice as future work.
- The roadmap has now been refreshed so the remaining deferred items live in explicit later work (`Slice 3+` domain expansion, later harness-adaptation slices, and `Slice 6`) instead of being implied as shipped Slice 2 work.

## Best Next Starting Points

- For continuing the current hardening pass, start with:
  - [docs/superpowers/specs/2026-04-09-llm-reliability-and-finance-hardening-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-09-llm-reliability-and-finance-hardening-design.md)
  - [docs/superpowers/plans/2026-04-09-llm-reliability-and-finance-hardening.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/plans/2026-04-09-llm-reliability-and-finance-hardening.md)
  - [minx_mcp/core/interpretation](/Users/akmini/Documents/minx-mcp/minx_mcp/core/interpretation)
  - [minx_mcp/core/goal_capture.py](/Users/akmini/Documents/minx-mcp/minx_mcp/core/goal_capture.py)
  - [minx_mcp/finance/analytics.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/analytics.py)
  - [minx_mcp/finance/importers.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/importers.py)
  - [minx_mcp/finance/service.py](/Users/akmini/Documents/minx-mcp/minx_mcp/finance/service.py)
- For Slice 3 planning, start with [docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-life-os-architecture-design.md), [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md), and the existing Finance domain patterns under [minx_mcp/finance](/Users/akmini/Documents/minx-mcp/minx_mcp/finance).
- For later harness-specific adaptation work, start with [docs/superpowers/specs/2026-04-08-slice2-1-conversational-goals-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-08-slice2-1-conversational-goals-design.md), [docs/superpowers/specs/2026-04-08-slice2-1-trust-hardening-design.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-08-slice2-1-trust-hardening-design.md), and [docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md](/Users/akmini/Documents/minx-mcp/docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md).
- Before making changes, rerun:
  - `.venv/bin/python -m pytest tests/test_core_server.py tests/test_goal_progress.py tests/test_goals.py tests/test_detectors.py tests/test_llm.py tests/test_core_mcp_stdio.py -q`
  - `.venv/bin/python -m pytest tests -q`
  - `.venv/bin/python -m mypy`
