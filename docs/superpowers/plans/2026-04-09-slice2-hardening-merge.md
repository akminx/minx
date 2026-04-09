# Slice 2 Hardening Merge Notes

**Date:** 2026-04-09
**Status:** Completed

## Purpose

Record what the Slice 2 hardening follow-up actually accomplished so later agents do not treat the old hardening plan as still pending work.

## What Landed

- Goal filter validation now rejects blank or whitespace-only `category_names`, `merchant_names`, and `account_names` members.
- Protected `daily_review` attention semantics were tightened so `goal_attention_level` reflects only `watch` or `off_track` goals, and spending attention reflects real protected pressure instead of ordinary spending.
- The wheel-packaging verification path was fixed so the full repo test suite is honestly green in the uv-managed environment.
- `goal_capture` no longer relies on `assert` for payload invariants that would disappear under `python -O`.
- `finance/import_workflow.py` now logs the previously swallowed `OSError` path during canonicalization.
- Repo docs were refreshed so Slice 2.1 is described as implemented for the repo-scoped Core work, with harness-specific setup still deferred.

## Follow-Up Review Fixes

The follow-up review after the hardening pass closed two more coherence gaps:

- protected review spending attention now still surfaces coarse finance pressure when a real `finance.spending_spike` detector signal exists, even if spending is categorized
- above-target goal watch summaries now use wording that matches the metric direction instead of limit-oriented wording

## Verification

Latest verification from the current branch baseline:

- `uv run python -m pytest tests -q` -> `346 passed`
- `uv run python -m mypy` -> clean

## Notes

- This file is historical merge context, not an active execution plan.
- Slice 3 remains the recommended next implementation slice.
- Harness-specific instance setup remains intentionally deferred outside this repo-scoped Slice 2.1 work.
