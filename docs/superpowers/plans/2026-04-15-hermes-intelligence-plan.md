# Hermes Intelligence Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Hermes a genuine Life OS assistant by closing the loop between MCP signals and user action, replacing passive reporting with triage-driven conversation, and wiring up data entry flows that feed back into MCP.

**Governing rule:** All computation here lives in Hermes (skills, state, LLM interpretation). MCP stays deterministic. The only MCP changes in this plan are new cross-domain detectors (Phase 5).

**Prerequisite:** None. All phases can start immediately against the current MCP surface. Phase 5 (cross-domain detectors) requires the code quality cleanup to be complete.

---

## Phase 1: `minx/goal-capture` Skill (Highest Priority)

All infrastructure is already built in MCP. This phase adds only a Hermes conversation driver.

### What it does
Turns natural language goal management into a multi-turn conversation:
- `goal_parse` handles NL input, ambiguity resolution, LLM fallback, and structured validation
- `resume_payload` in `GoalCaptureResult` carries partial state between turns
- The skill drives: user speaks → `goal_parse` → clarify if needed → write to MCP → confirm back

### Tasks
- [ ] Create `~/.hermes/skills/minx/goal-capture/SKILL.md`
- [ ] Define the workflow:
  1. User sends a natural language goal message (or update/archive intent)
  2. Call `minx_core.goal_parse(message=user_input)`
  3. If result `action == "clarify"`: surface `question` and `options` to user; wait for selection; call `goal_parse(structured_input=resume_payload + user_choice)`
  4. If result `action == "create"`: call `minx_core.goal_create(payload)`, confirm to user
  5. If result `action == "update"`: resolve `goal_id` from context or ask user; call `minx_core.goal_update(goal_id, payload)`, confirm
  6. If result `action == "archive"`: call `minx_core.goal_archive(goal_id)`, confirm
  7. On any action: optionally call `minx_core.get_goal_trajectory(goal_id)` and show current state
- [ ] Handle the `goal_id` resolution case: when `goal_parse` returns an update/archive but no `goal_id`, call `minx_core.goal_list(status="active")` and ask user to pick
- [ ] Add the skill to the `weekly-review` flow: after the review, prompt "want to create or update any goals based on this?"
- [ ] Test: create a goal via NL, update it, archive it — verify MCP state is correct after each

### Why first
Zero new MCP work. Completes the bidirectional loop: user speaks → MCP stores → detectors fire → Hermes reports. Every existing goal detector becomes actionable in a single conversation.

---

## Phase 2: Triage Snapshot (Restructure Reporting Skills)

Replace the weekly-review reporting pattern with a triage-first frame that creates conversation rather than walls of text.

### What it does
`DailySnapshot` already has `attention_items` sorted by severity, `InsightCandidate.severity` (alert > warning > info), and `InsightCandidate.actionability` (action_needed > suggestion). Use this structure to produce a prioritized triage list instead of a domain-by-domain dump.

### Tasks
- [ ] Update `~/.hermes/skills/minx/weekly-review/SKILL.md` triage framing:
  1. Call `get_daily_snapshot` — extract all signals, sorted by severity
  2. Call `get_insight_history(days=7)` — identify which signals are recurring (check `recurrences > 1`)
  3. Call `get_goal_trajectory` for any goal flagged as `off_track` in the snapshot
  4. Produce output in triage order:
     - **Acute** (new `alert`-severity signals, `action_needed`)
     - **Worsening trends** (trajectory `trend: worsening` on off-track goals)
     - **Chronic** (recurring signals with `recurrences >= 3`, framed as patterns not alerts)
     - **Stable** (everything on-track, summarized in one line)
  5. End with: "Which of these do you want to deal with first?" — opening the action loop
- [ ] Update `midweek-health-check` cron to use the same triage framing for training signals
- [ ] Do NOT add new MCP calls — this is a reframing of data already pulled

---

## Phase 3: Signal Disposition Tracking (Hermes-Side State)

Track what happens to surfaced signals so Hermes can modulate behavior: escalate chronic signals, stop repeating dismissed ones.

### What it does
Hermes maintains a `signal_dispositions` store (Hermes state DB). When it surfaces a signal, it records `signal_id + detector_type + surfaced_at`. When the user acknowledges, dismisses, or acts, Hermes updates the disposition. Future triage checks this state before framing.

### Disposition states
```
surfaced → acknowledged → acted_on
                        → dismissed
                        → recurring_ignored  (dismissed 3+ times)
```

### Tasks
- [ ] Add `signal_dispositions` tracking to Hermes state (use Hermes's existing `state.db` or a skill-local JSON file, whichever Hermes supports natively)
- [ ] In `weekly-review` and `daily-triage` (Phase 4), after surfacing a signal: record `{signal_type, detector_id, surfaced_at, session_id}` in dispositions
- [ ] Add disposition context to triage framing:
  - `dismissed` once: lower priority, softer tone
  - `dismissed` 3+ times → `recurring_ignored`: reframe as "chronic pattern to watch" not an alert; mention recurrence count
  - `acted_on`: mention that the user addressed this last time, ask if they want to follow up
- [ ] Add a way for user to explicitly dismiss: "ignore this" → sets disposition to `dismissed`
- [ ] Cross-reference `get_insight_history.recurrences` with disposition state: if MCP says recurrences=5 but disposition says never acted, escalate framing ("this has fired 5 weeks in a row")

---

## Phase 4: Signal-Driven `daily-triage` Cron (Pre-Slice 8)

Add a cron job that proactively pings only when there's something worth acting on. Silence when things are normal.

### What it does
One evening cron that calls `get_daily_snapshot`, checks for actionable signals, and sends a Discord DM only if the threshold is met. This is the predecessor to Slice 8's `daily_review` playbook — same behavior, simpler infrastructure.

### Tasks
- [ ] Create new Hermes cron job `daily-triage`:
  - Schedule: `0 21 * * *` (9 PM daily)
  - Skill: `minx/weekly-review` (reuse triage framing from Phase 2) or inline prompt
  - Deliver: Discord DM
- [ ] Add a **silence gate** to the skill: if `snapshot.signals` is empty AND `attention_items` is empty → log "nothing actionable" and exit without sending Discord message
- [ ] Threshold for notification: any `severity: alert` signal OR any `attention_items` with `actionability: action_needed`
- [ ] **Remove or reschedule `midweek-health-check`**: it fires on a fixed schedule regardless of signal state. Replace with signal-driven triage or keep only as a fallback "weekly pulse" if the daily-triage has been silent for 7 days
- [ ] When Slice 8 ships: this cron becomes the `daily_review` playbook with `log_playbook_run` audit trail. The behavior is identical; the infrastructure improves.

---

## Phase 5: Conversation-Driven Data Entry (Meals + Training)

Extend the `goal_parse` pattern — LLM extraction in Hermes, structured write to MCP — to the two highest-friction entry points.

### 5a: Meal Logging via Conversation

- [ ] Create `~/.hermes/skills/minx/meal-log/SKILL.md`
- [ ] Workflow:
  1. User says e.g. "I had chicken salad for lunch, probably 40g protein"
  2. Hermes LLM extracts: `meal_kind` (breakfast/lunch/dinner/snack), `summary`, optional `protein_grams`, `calories_kcal`, `carbs_grams`, `fat_grams`
  3. Call `minx_meals.meal_log(meal_kind=..., occurred_at=now, summary=..., protein_grams=...)`
  4. If a `nutrition_profile` exists: call `nutrition_summary(date=today)` and show progress toward daily targets
  5. Confirm to user
- [ ] Add the skill as an available action from triage: when `detect_skipped_meals` or `detect_low_protein` fires, offer "want to log a meal?"

### 5b: Training Session Logging via Conversation

- [ ] Create `~/.hermes/skills/minx/training-log/SKILL.md`
- [ ] Workflow:
  1. User says e.g. "Did bench press 3x8 at 80kg and squats 4x6 at 100kg"
  2. Hermes LLM extracts exercise names + sets
  3. For each exercise: call `minx_training.training_exercise_list()` to resolve canonical name; if ambiguous, ask user
  4. Call `minx_training.training_session_log(entries=[{exercise_id, sets: [{reps, weight_kg}]}])`
  5. If an active program exists: show progress toward program targets
  6. Confirm to user
- [ ] Integrate with `health-log` skill: make session logging available in-conversation after checking progress

---

## Phase 6: Cross-Domain Coherence Detectors (New MCP Work)

These are new deterministic detectors in Core that surface *incoherence* between behavior across domains. Unlike Phase 1-5 (pure Hermes), these require new MCP detector code.

**Prerequisite:** Code quality cleanup (Phase 1 bugs) should be complete before adding new detectors.

### Target detectors

- [ ] `cross.supplement_spending_without_training` — triggers when: `finance` shows spending at supplement/gym merchants in the last 14 days AND `training` shows < 2 sessions logged. Requires `FinanceReadInterface.get_merchant_spending` + `TrainingReadInterface.get_training_summary`.
- [ ] `cross.delivery_spending_with_pantry_expiring` — triggers when: `finance` shows restaurant/delivery spending in the last 7 days AND `meals` pantry has items expiring within 3 days. Requires `FinanceReadInterface` + `MealsReadInterface.get_pantry_expiring`.
- [ ] `cross.goal_spending_drift` — triggers when: a spending-category goal is `off_track` AND the same category has had transactions in the last 7 days. This is a sharper version of the existing `category_drift` detector — it surfaces only when the goal itself is at risk, not just when spending is high. Already partially wired through existing goal + finance detectors; evaluate whether a new detector or refinement of `detect_category_drift` is the right fix.

### Tasks for each detector
- [ ] Write failing test asserting the detector fires under the trigger condition and is silent otherwise
- [ ] Implement the detector function in `core/detectors.py` (following the existing pattern)
- [ ] Register it in the detector registry with deterministic ordering and metadata tags
- [ ] Run full test suite

---

## Phase 7: Memory as Framing Context (Slice 6 Integration)

When Slice 6 ships, memories should modulate how Hermes frames MCP signals — not just recall facts. This phase defines the integration pattern.

### Design (implement when Slice 6 is available)

- [ ] In `weekly-review` and `daily-triage`: before framing signals, call `minx_core.memory_list(status="active", type="pattern")` to load active pattern memories
- [ ] For each `alert`-severity signal: check if any memory describes the triggering behavior as expected/normal (e.g., "user buys coffee every Monday"). If match: downgrade framing from alert to context, note the pattern.
- [ ] For `detect_skipped_meals` on breakfast: check for a fasting/IF memory. If present: suppress the signal entirely.
- [ ] Disposition tracking (Phase 3) feeds Slice 6: when a signal is dismissed repeatedly, Hermes creates a memory candidate: "user consistently dismisses [detector_type] alerts" → surfaces to user for confirmation via `minx_core.get_pending_memory_candidates`
- [ ] Wiki maintenance playbook (Slice 8d): uses memories to generate Obsidian pages about patterns, entities, and behaviors. The Karpathy LLM Wiki pattern — memories are the structured facts, wiki pages are the LLM's synthesis.

---

## Execution Order and Dependencies

```
Phase 1 (goal-capture)     ─── start immediately, no dependencies
Phase 2 (triage snapshot)  ─── start immediately, independent of Phase 1
Phase 3 (disposition)      ─── depends on Phase 2 (needs triage skill to instrument)
Phase 4 (daily-triage)     ─── depends on Phase 2 (needs triage framing)
Phase 5a (meal-log)        ─── independent, start anytime
Phase 5b (training-log)    ─── independent, start anytime
Phase 6 (detectors)        ─── depends on code quality cleanup Phase 1 (bugs)
Phase 7 (memory framing)   ─── depends on Slice 6
```

| Phase | Effort | Where | Risk |
|-------|--------|-------|------|
| 1: goal-capture skill | 2-3 hours | Hermes | Low |
| 2: triage snapshot | 1-2 hours | Hermes | Low |
| 3: disposition tracking | 2-3 hours | Hermes | Low-Medium |
| 4: daily-triage cron | 1 hour | Hermes | Low |
| 5a: meal-log skill | 1-2 hours | Hermes | Low |
| 5b: training-log skill | 2-3 hours | Hermes | Low |
| 6: coherence detectors | 3-4 hours | MCP | Medium |
| 7: memory framing | 2-3 hours | Hermes | Low (additive) |

**Total: ~16-21 hours across multiple sessions.**

**Recommended first session:** Phase 1 (goal-capture) + Phase 2 (triage snapshot) + Phase 4 (daily-triage cron). ~4-6 hours. Pure Hermes work. Creates the first closed bidirectional loop and eliminates scheduled-but-empty notifications.
