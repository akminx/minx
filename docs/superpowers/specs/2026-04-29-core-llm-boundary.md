# Core LLM Boundary

**Date:** 2026-04-29
**Status:** Design decision
**Related:** MCP Render Contract, Slice 2.5 Core/Harness split, Slice 9 investigations

## Decision

Core may call LLMs only for structured interpretation. Hermes owns final user-facing prose, conversational timing, scheduling, reminders, and narrative voice.

Allowed Core LLM outputs:

- intents and actions
- normalized filters
- clarification type and slots
- structured proposal payloads
- confidence scores
- template identifiers
- JSON-compatible event slots

Disallowed Core LLM outputs:

- final assistant wording
- daily-review narrative prose
- apology, coaching, encouragement, or voice/personality copy
- channel-specific follow-up text
- hidden scheduling decisions that should be visible to Hermes

## Current Carve-Outs

`finance_query` and `goal_parse` may keep LLM-backed structured interpretation in Core because they translate untrusted natural language into validated tool-shaped data. Their model output must pass schema validation and must be rendered by Hermes using `*_template` and `*_slots`.

Investigation tools may accept structured step/event JSON from Hermes, but Core stores digests, citations, status, response templates, and response slots. Core must not store raw tool output or model-authored explanation text as durable user-facing prose.

## Enforcement Rules

- Core result models should not include `assistant_message`, `narrative`, or similar final-prose fields.
- LLM helpers in Core should expose JSON/structured prompt APIs, not review/narrative APIs.
- Tests for LLM paths should assert that model-authored copy does not appear in template identifiers or slots.
- If a new tool needs conversational copy, add a render template and slots instead of adding prose fields.

## Escalation Rule

If a proposed Core LLM feature needs judgment about tone, timing, relationship context, or whether to interrupt the user, move that behavior to Hermes. If it only maps input into deterministic state mutation or query parameters, Core can own it with schema validation and audit logs.
