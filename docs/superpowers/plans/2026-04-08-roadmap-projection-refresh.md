# Minx Roadmap Projection Refresh Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the Minx roadmap with the Life OS north star while preserving the reality of the current repo baseline and making deferred work explicit in later slices.

**Architecture:** Treat the existing repo as the portable Core foundation, keep domain ownership outside Core, and sequence future work by trust: first finish the reusable Core goal/review boundary, then expand domains, then add harness adaptation and durable memory, and only after that push further into autonomy and dashboard surfaces.

**Tech Stack:** Python 3.12, SQLite, FastMCP, existing Minx Core/Finance modules, future Hermes or Discord harness integration, future domain MCP servers

---

## File Structure

**Modify**

- `docs/superpowers/specs/2026-04-06-minx-roadmap-slices.md`
  Canonical slice roadmap and dependency graph.
- `HANDOFF.md`
  Working-tree reality and explicit deferrals for the next agent.
- `README.md`
  User-facing summary of shipped capabilities and known limits.

**Create**

- `docs/superpowers/plans/2026-04-08-roadmap-projection-refresh.md`
  Recommended execution order from the current repo baseline.

---

## Task 1: Lock The Current Baseline

**Goal:** Treat the current repo as the end of Slice 2 Core scope, not as a partially hidden version of later slices.

- [ ] Confirm Slice 1 remains the structured review/event foundation.
- [ ] Confirm Slice 2 is framed as Core goals + drift + goal-aware review, not Hermes conversation, durable memory, or autonomy.
- [ ] Keep repo-shipped behavior and roadmap-deferred behavior clearly separated in docs.
- [ ] Preserve the current local-first single-user assumptions as part of the baseline.

**Exit criteria:**
- A reader can tell what is actually shipped today versus what is intentionally deferred.

---

## Task 2: Add The Missing Bridge Slice

**Goal:** Insert the bridge slice the product needed before broader expansion, while keeping its reusable Core work distinct from later harness-specific setup.

- [ ] Add `Slice 2.1: Conversational Goals + Trust Hardening`.
- [ ] Put transport-agnostic Core conversational goal capture there, with Hermes/Discord instance setup deferred later.
- [ ] Put stronger sensitivity/redaction policy there instead of leaving it as “follow-up polish.”
- [ ] Define this slice as the trust and interaction bridge between Core goals and broader surfaces.

**Why this matters:**
- It matches the architecture doc's “Hermes-like shell, Core-owned logic” posture.
- It keeps sensitive review content from leaking into richer surfaces before trust policy exists.

**Exit criteria:**
- The roadmap has an explicit home for Hermes goal capture and review redaction work.

---

## Task 3: Keep Domain Expansion Focused

**Goal:** Make the middle of the roadmap about real cross-domain value, not premature surface polish.

- [ ] Keep `Slice 3: Meals MCP` as the first domain expansion step.
- [ ] Keep `Slice 4: Training MCP` as the second domain expansion step.
- [ ] Describe those slices as the path to meaningful cross-domain read models and detectors.
- [ ] Avoid bundling memory, dashboard, or autonomy work into those domain slices.

**Exit criteria:**
- The roadmap clearly shows that cross-domain value comes from more domains first, not from UI or autonomy first.

---

## Task 4: Separate Interaction From Durable Intelligence

**Goal:** Prevent harness adaptation, ambient inputs, and durable memory from collapsing into one vague “smartness” milestone.

- [ ] Keep `Slice 5` focused on harness adaptation and ambient inputs.
- [ ] Keep `Slice 6` focused on durable memory, insight expiration, and review reproducibility.
- [ ] Explicitly place read-model snapshot persistence and insight expiration in Slice 6.
- [ ] Make it clear that Slice 5 improves interaction posture, while Slice 6 improves trust, explanation, and historical grounding.

**Exit criteria:**
- The roadmap shows a clean separation between interaction-layer work and long-term memory/reproducibility work.

---

## Task 5: Keep Journal As A Real Domain

**Goal:** Preserve journal and idea capture as a first-class domain, not just a temporary poll-adapter side channel.

- [ ] Keep `Slice 7: Ideas/Journal MCP` in the roadmap.
- [ ] Describe it as the structured replacement for ad hoc ambient ingestion where appropriate.
- [ ] Position it as a reflection domain that enriches cross-domain review rather than as a generic notes dump.

**Exit criteria:**
- The roadmap treats journal data as a durable domain with owned facts, not just background context.

---

## Task 6: Make The Late Slices Depend On Trust

**Goal:** Ensure autonomy and dashboard work happen after the system is trustworthy enough to deserve them.

- [ ] Keep `Slice 8` focused on bounded playbooks, audit trails, and explicit control.
- [ ] Keep `Slice 9` focused on richer surfaces built on the same review/state system.
- [ ] State clearly that autonomy depends on goals, trust policy, harness adaptation, and durable memory.
- [ ] State clearly that dashboards depend on stable review artifacts and domain read models rather than parallel business logic.

**Exit criteria:**
- A future engineer can see why autonomy and dashboards are late slices instead of “just another client.”

---

## Recommended Execution Order

- [ ] Treat repo-scoped `Slice 2.1` as the completed Core bridge once `goal_capture` and the protected review boundary are shipped.
- [ ] Execute `Slice 3` and `Slice 4` next if the priority is stronger cross-domain insight before more harness-specific client polish.
- [ ] Land `Slice 5` before trying to make every harness feel equally good.
- [ ] Land `Slice 6` before taking on serious autonomy or dashboard history/debug needs.
- [ ] Treat `Slice 8` and `Slice 9` as capstone work, not as baseline product work.

---

## Outcome

When this roadmap is followed, Minx grows in the same order the architecture doc implies:

1. portable Core foundation
2. goals and review interpretation
3. safe reusable Core-side conversational/trust integration
4. multiple real domains
5. harness-aware behavior
6. durable memory and reproducibility
7. bounded autonomy
8. richer surfaces

That is the path most likely to produce a trustworthy Life OS instead of a pile of partially connected features.
