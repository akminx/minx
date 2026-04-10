# Minx Life OS Architecture Design

**Date:** 2026-04-06
**Status:** Drafted for review
**Scope:** North-star architecture for Minx as a personal Life OS across multiple domains and agent harnesses

## Goal

Define the overall architecture for Minx as a portable, agentic Life OS that helps capture daily life, maintain durable cross-domain memory, generate useful insights, and gradually grow into bounded autonomy without collapsing into one bloated monolith.

This document is not an implementation spec for the whole system in one pass. It is the architectural north star that future slice specs should follow.

## Product Vision

Minx is one assistant with one identity, one voice, and one cross-domain picture of the user. It is not a set of unrelated bots for finance, meals, health, and ideas.

Minx should help the user:

- capture what happened
- understand how they are doing
- review progress against goals
- plan what matters next
- grow into selective, trustworthy autonomy over time

Minx should feel like a Hermes-style agent from the user side, while remaining portable across harnesses such as Hermes, Claude Code, Codex, OpenCode, and future interfaces.

## Success Criteria

This architecture is successful when:

- Minx has one portable core that is not locked to any single agent harness
- domain systems remain independently usable through MCP
- cross-domain insights are derived from structured facts rather than raw chat vibes
- memory stays useful and queryable without becoming an unbounded transcript dump
- the same underlying state can drive Discord, CLI, and future dashboard experiences
- autonomy grows through explicit playbooks and policies rather than a vague always-on super-agent loop
- new domains can be added without restructuring the whole system

## Non-Goals

This architecture does not attempt to:

- implement every Minx feature in one phase
- make Minx a general unrestricted computer-use agent in v1
- treat raw conversation history as the sole long-term memory system
- force every interaction through a central proxy hop
- duplicate domain facts inside Minx Core
- make the web UI a prerequisite for early value

## Core Architectural Decision

Minx should be built as:

- one `Minx Core` portable intelligence and state layer
- multiple domain MCP servers with clear ownership
- an event-driven integration model
- a harness adaptation layer inside Minx Core
- one or more agent shells that make Minx feel conversational and agentic

The recommended pattern is:

- domains own facts
- Minx Core owns interpretation
- harnesses own conversation style and execution feel
- surfaces own presentation

## Top-Level Architecture

```text
User Surfaces
  Discord | CLI | Web UI | future clients
        |
        v
Agent Harness
  Hermes today, other harnesses later
        |
        v
Minx Core
  identity | goals | memory policy | retrieval orchestration
  read models | insight engine | playbooks | harness adaptation
        ^
        |
Shared Event Stream / Event Tables
        ^
        |
Domain MCP Servers
  Finance | Meals | Training | Ideas/Journal | future domains
```

Users may also call domain MCP servers directly when needed. Minx Core must still receive meaningful events from those domains so its picture of the world remains current even when a harness bypasses Minx for a direct domain action.

## Why Event-Driven Instead Of Proxy-First

Minx Core should not be a mandatory proxy in front of every domain tool call.

The event-driven model is preferred because it:

- preserves independent use of each domain MCP
- avoids adding a second hop to every request
- prevents Minx Core from becoming a bottleneck
- keeps cross-domain state current even when other harnesses call domains directly

The cost is the need for a stable event model, but that complexity is worth it because it protects portability and future-proofing.

## Domain Model

The first recommended Minx domains are:

- `Finance MCP`
- `Meals MCP`
- `Training MCP`
- `Ideas/Journal MCP`

`Training MCP` is preferred over a vague `Health MCP` for early scope. Training is concrete and structured enough to implement cleanly. Broader health can grow later around it.

### Domain Ownership

`Finance MCP` owns:

- transactions
- accounts
- categories
- spending reports
- financial facts

`Meals MCP` owns:

- meal logs
- foods and ingredients
- recipes
- nutrition and macro facts
- meal plans and pantry-adjacent structures later

`Training MCP` owns:

- workout plans
- exercise library
- session logs
- sets, reps, load, intensity
- progression and recovery signals

`Ideas/Journal MCP` owns:

- captured ideas
- journal entries
- reflections
- linked references
- long-form personal context

## Shared Versus Local Responsibilities

### Shared Platform

The shared Minx platform should own:

- MCP response contracts
- event publishing contracts
- jobs and scheduling
- audit logging
- auth and permissions
- preferences and user profile
- common ID and timestamp conventions
- transport helpers where useful

The shared layer should stay small and boring. Its job is consistency, not domain intelligence.

### Local Domain Logic

Each domain should own:

- its own schema
- its own tools
- its own normalization and parsing logic
- its own source-of-truth records
- its own domain-specific reports and workflows

### Minx Core

Minx Core should own only cross-domain and assistant-level concerns:

- goals
- routines
- memory promotion policy
- retrieval orchestration
- read models
- insight generation
- daily and weekly review state
- next-day planning support
- bounded autonomy playbooks
- harness adaptation policies

Minx Core should not duplicate or replace the source-of-truth domain data.

## Source Of Truth Hierarchy

Each fact type must have one source of truth.

- finance facts live in `Finance MCP`
- meal and nutrition facts live in `Meals MCP`
- workout and progression facts live in `Training MCP`
- idea and journal facts live in `Ideas/Journal MCP`
- cross-domain goals, derived state, and insight records live in `Minx Core`

Obsidian should be treated as a durable human-facing projection layer for notes, reviews, and long-form records. It should not be the only machine-readable source of truth for core Minx state.

## Event Model

Cross-domain intelligence depends on a stable event contract.

Each domain should emit meaningful events such as:

- `finance.transaction_imported`
- `finance.summary_updated`
- `meal.logged`
- `nutrition.day_updated`
- `workout.completed`
- `training.program_updated`
- `idea.captured`
- `journal.entry_added`

An event contract should include at minimum:

- `event_type`
- `domain`
- `occurred_at`
- `recorded_at`
- `entity_ref`
- `source`
- `payload`
- `sensitivity`

Domains should emit events on meaningful state changes, not on every incidental internal operation.

## How Cross-Domain Insights Work

Cross-domain insights should be produced as a pipeline:

1. domains emit facts as events
2. Minx Core builds structured read models from those facts
3. detectors generate insight candidates
4. a reasoning layer ranks or contextualizes what matters
5. a narration layer expresses the result in Minx's voice

This means Minx does not rely on a single opaque prompt to discover everything at once.

### Read Models

Minx Core should build read models such as:

- daily timeline
- daily nutrition snapshot
- weekly restaurant spend
- workout adherence trend
- goal progress state
- recurring behavior patterns
- open loops and unfinished intentions

### Insight Records

Insights should be stored as first-class records with fields such as:

- `insight_type`
- `summary`
- `supporting_signals`
- `confidence`
- `severity`
- `actionability`
- `expires_at`

This lets Minx explain why it surfaced something and makes it easier to evaluate or regenerate insights later with better models.

## Intelligence Architecture

Minx intelligence should be layered:

- `State layer`
  - events
  - goals
  - routines
  - read models
  - durable memory

- `Reasoning layer`
  - rule-based detectors
  - threshold checks
  - ranking
  - LLM evaluation where useful

- `Expression layer`
  - Discord digest
  - vault note
  - dashboard cards
  - conversational coaching

The recommended strategy is `hybrid intelligence`:

- rules and structured state generate candidates
- an LLM ranks, contextualizes, and explains

This is preferred over both a purely deterministic system and a purely LLM-first system.

## Memory And Retrieval

Minx should not treat all raw chat as durable memory.

Instead, Minx should separate:

- `structured durable memory`
- `searchable history`
- `domain facts`
- `ephemeral session context`

### Durable Memory Should Include

- goals
- routines
- preferences
- stable constraints
- recurring meals
- recurring workout structures
- recurring spending patterns
- durable insights worth revisiting

### Searchable History Should Include

- prior chat transcripts where available
- journal notes
- captured references
- imported content
- long-form notes

Minx should still be able to retrieve things the user previously said or referenced, but retrieval should come from the right layer rather than pretending every chat turn is sacred long-term memory.

### Memory Promotion Policy

Minx should automatically promote low-risk useful patterns into durable memory, such as:

- recurring meals
- recurring exercise names or structures
- soft preferences
- repeat reference links

Minx should ask before promoting more identity-level or commitment-level memories, such as:

- major long-term goals
- personal constraints with strong implications
- deeply personal identity claims

## MCP / Harness Responsibility Split

Minx is portable because the MCP protocol is the adaptation layer. The MCP does not need to know which harness is calling it. It returns structured data; the harness decides how to present it.

### MCP Owns

- domain data (CRUD, persistence, migrations)
- deterministic detectors (threshold-based signal generation)
- historical insight log (what was detected, when)
- temporal aggregations (trends, trajectories, week-over-week)
- read model snapshots (structured data for harness consumption)
- data-integrity LLM work (classification, parsing, entity resolution)
- vault persistence mechanics (atomic writes, path safety)
- goal records, progress computation, and trajectory

### Harness Owns

- when to check in (cron, proactive triggers, scheduling)
- what to tell the user (prioritization, suppression, personalization)
- how to say it (narrative, voice, channel-appropriate format)
- conversation memory (what was said before, what user responded)
- cross-domain reasoning (connecting signals the MCP surfaces)
- coaching and planning (goal guidance, next actions)
- multi-turn dialogue (clarification, follow-up, confirmation)
- where to post (Discord channels, vault notes, dashboards)

### The Governing Rule

If the logic depends on the user's data or goals, it belongs in the MCP. If the logic depends on how you are talking to the user, it belongs in the harness.

### Dual-Path Tool Pattern

MCP tools that involve LLM interpretation (query parsing, goal intent extraction, categorization) should accept both structured and natural language input. A smart harness can pre-parse with its own LLM and pass structured params directly. A dumb harness or cron job passes natural language and lets the MCP's internal LLM handle it. Same tool, same data outcome, works everywhere.

### Portability

Harness adaptation is not a feature Minx Core needs to build. The MCP protocol itself provides portability. Any harness that speaks MCP can call the tools and get structured data. The harness adapts its own behavior — Minx Core does not need a harness registry, behavior profiles, or per-harness configuration.

Harness-specific skills (e.g. Hermes skill files) are thin wiring that maps MCP tools to the harness's interaction model. They should contain routing and formatting, not domain logic. When the harness changes, only the wiring rewrites — the MCP and its data are unchanged.

## Autonomy Strategy

Autonomy should grow as a thin layer on top of the Life OS, not as the foundation of the system.

The autonomy ladder should be:

1. passive capture
2. reflective review
3. suggested actions
4. guardrailed low-risk automation
5. delegated workflows
6. selective proactive autonomy

Every autonomous behavior must have:

- a clear trigger
- a bounded action
- a success metric
- a way to disable it
- a clear owner

If a behavior cannot meet these constraints, it should not ship.

## First Autonomous Playbook

The first Minx playbook should be:

`daily review -> goal check -> next-day plan draft`

This playbook is recommended because it is:

- cross-domain
- useful every day
- low-risk
- naturally aligned with the Life OS vision

The preferred launch surface is:

- short digest in Discord
- full saved note in the vault

The preferred input shape for the first playbook is:

- manual check-ins and captures
- finance signals
- ideas and journal captures
- future meals and training signals as those domains come online

The daily review should optimize for a balanced output:

- what happened
- how the user is doing against goals
- what patterns are emerging
- what tomorrow should focus on

## Anti-Bloat Guardrails

Minx stays healthy only if its boundaries remain hard.

### What Belongs In Minx Core

- cross-domain goals
- daily and weekly state
- retrieval orchestration
- insight generation
- memory promotion policy
- bounded shared playbooks

### What Must Stay Out Of Minx Core

- raw finance logic
- raw meal parsing
- raw workout logging internals
- UI-specific code
- harness-specific memory hacks
- unrestricted computer-use automation

### Admission Rule For New Features

A new early-stage Minx feature should only be added if it clearly improves one of:

- capture
- review
- planning
- goal alignment

If it does not improve one of those, it does not belong in early Minx.

## Recommended Roadmap Decomposition

This architecture is too large for one implementation plan. It should be decomposed into slice specs.

Recommended order:

1. shared event contract and platform support
2. Minx Core read models and goal state
3. daily review and next-day planning playbook
4. harness adaptation registry and policy layer
5. Meals MCP first slice
6. Training MCP first slice
7. deeper retrieval and durable memory promotion
8. limited proactive autonomy
9. dashboard and richer UI surfaces

Each of those should get its own spec, plan, and implementation cycle.

## Testing And Evaluation Implications

Cross-domain intelligence must be testable.

The system should support evaluation at multiple layers:

- event contract tests
- read model tests
- detector tests
- retrieval tests
- end-to-end review generation tests

Minx should eventually maintain a small corpus of expected scenarios, such as:

- takeout spend rises while meal planning drops
- protein stays low while training adherence remains high
- goals drift for three days and should trigger a gentle nudge
- one bad day should not produce an overreactive insight

These scenarios will help Minx stay stable as prompts, models, and harnesses evolve.

## Open Architectural Principle

Minx should remember like a database, think like a pipeline, and speak like an assistant.

That principle is the simplest guide for future decisions:

- durable data over prompt-only memory
- explicit pipelines over vague magic
- one assistant experience over fragmented tools

## Next Step

The next spec should not attempt the entire architecture. It should define the first implementation slice for:

- shared event contracts
- Minx Core read models
- the first daily review and next-day planning playbook
- Hermes integration for the first agentic user experience
