# Hermes Capability Augmentation Roadmap (Minx)

> Historical roadmap: this document predates the current `minx-hermes` investigation runner and should not be used as the authoritative architecture or operations guide. Use `../README.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, and the current `minx-hermes` README for shipped behavior.

Date: 2026-04-14

## Current Baseline
- Active local skills: `finance-import`, `finance-report`, `health-log`, `journal-scan`, `weekly-review`
- Active cron jobs: `journal-scan`, `midweek-health-check`
- Enabled MCPs: `minx_finance`, `minx_core`, `minx_meals`, `minx_training` (+ `qmd`, `obsidian`)

## Highest-Value Gaps
1. Finance operations after import are thin (categorization cleanup, rules tuning, anomaly triage, reconciliation).
2. Goals lifecycle is underused (`goal_create`, `goal_update`, `goal_parse`, `goal_archive` exist but are not workflowed).
3. Meals MCP has rich pantry/recipe/nutrition tools but only `meal_log` is in active workflows.
4. Training MCP supports program lifecycle but current skill focuses mostly on session logging.
5. No standardized “upgrade safety” workflow to guard future migrations/cutovers.

## Recommended New Skills (Priority Order)

1. `minx/finance-triage`
- Purpose: clean uncategorized transactions, apply category rules, rerun monitoring.
- Core tools: `finance_query`, `finance_categorize`, `finance_add_category_rule`, `finance_monitoring`, `finance_anomalies`.

2. `minx/finance-reconcile`
- Purpose: period-close checklist (import completeness + account-level sanity checks + report parity).
- Core tools: `safe_finance_accounts`, `finance_monitoring`, `finance_generate_monthly_report`, optional `sensitive_finance_query`.

3. `minx/goal-manager`
- Purpose: parse natural-language goals, create/update/archive goals, and track drift.
- Core tools: `goal_parse`, `goal_create`, `goal_update`, `goal_get`, `goal_archive`, `get_goal_trajectory`.

4. `minx/meals-planner`
- Purpose: convert pantry + nutrition targets into weekly meal suggestions and shopping deltas.
- Core tools: `nutrition_profile_get`, `pantry_list`, `recipe_scan`, `recommend_recipes`.

5. `minx/pantry-watch`
- Purpose: monitor low-stock/expiring items and issue actionable restock nudges.
- Core tools: `pantry_list`, `persist_note` (optional digest logging).

6. `minx/training-program`
- Purpose: manage active programs and tie logs to program intent.
- Core tools: `training_program_upsert`, `training_program_activate`, `training_program_get`, `training_session_log`, `training_progress_summary`.

7. `minx/cutover-guard`
- Purpose: enforce migration-safe checks before enabling jobs/MCP changes.
- Checks: MCP reachability, tool smoke tests, snapshot integrity, paused-job inventory, rollback pointers.

## Recommended Job Additions

1. Weekly review automation
- Cadence: weekly (Sun evening)
- Skill: `minx/weekly-review`
- Output: single cross-domain summary with “next 3 actions”.

2. Finance triage automation
- Cadence: 2x/week
- Skill: `minx/finance-triage`
- Output: uncategorized count delta, applied rules, anomalies requiring manual review.

3. Pantry watch automation
- Cadence: daily morning
- Skill: `minx/pantry-watch`
- Output: expiring-soon + low-stock shortlist.

## Upgrade Workflow Hardening (for future updates)

1. Canonical “pre-change checklist”
- Export/backup: config, cron jobs, skills snapshot.
- Validate: `hermes mcp list`, `hermes skills list`, cron parse sanity.

2. Canonical “post-change verification”
- Run: MCP connectivity + one smoke call per active MCP domain.
- Assert: only intended skills/jobs active.
- Regenerate and inspect `.skills_prompt_snapshot.json`.

3. Keep a migration log in repo
- Add an append-only changelog for Hermes operational changes with timestamp, reason, rollback path, and verification result.

## Suggested 30-Day Execution Plan

1. Week 1: implement `minx/finance-triage` + schedule job.
2. Week 2: implement `minx/goal-manager` + integrate weekly-review goal section.
3. Week 3: implement `minx/meals-planner` and `minx/pantry-watch`.
4. Week 4: implement `minx/cutover-guard` and formalize pre/post upgrade runbooks.

