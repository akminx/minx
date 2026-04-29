# Render Template Registry

**Date:** 2026-04-29
**Status:** Small design proposal
**Related:** MCP Render Contract, Hermes renderer, goal/memory/investigation render hints

## Problem

Template identifiers are currently string literals such as `goal_parse.create.ready` and `memory_capture.created_candidate`. They are stable contracts, but without a registry, Core and Hermes can drift silently.

## Decision

Add a small shared registry before adding more conversational tools. Do not build a rendering framework in Core. The registry should define names and required slot keys only.

Recommended Core shape:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderTemplate:
    id: str
    required_slots: frozenset[str]


RENDER_TEMPLATES = {
    "goal_parse.create.ready": RenderTemplate(
        id="goal_parse.create.ready",
        required_slots=frozenset({"action", "goal_type", "period", "target_value"}),
    ),
}
```

Core responsibilities:

- import template IDs from the registry instead of writing raw strings in tool code
- validate required slots in tests
- keep slot values JSON-compatible
- never render final prose

Hermes responsibilities:

- map template IDs to copy
- choose tone and channel presentation
- tolerate additive optional slots
- fail loudly on unknown template IDs in development

## Compatibility

Template IDs are append-only contracts. If the meaning changes, add a new ID rather than changing the old one. Optional slots may be added without changing the ID.

## Initial Registry Contents

- `finance_query.clarify.missing_filter`
- `finance_query.clarify.missing_date_range`
- `goal_parse.create.ready`
- `goal_parse.update.ready`
- `goal_parse.no_match.unsupported`
- `goal_parse.clarify.ambiguous_goal`
- `goal_parse.clarify.ambiguous_subject`
- `goal_parse.clarify.missing_goal`
- `goal_parse.clarify.missing_target`
- `goal_parse.clarify.vague_intent`
- `memory_capture.created_candidate`
- `investigation.started`
- `investigation.step_logged`
- `investigation.needs_confirmation`
- `investigation.completed`
- `investigation.failed`
- `investigation.cancelled`
- `investigation.budget_exhausted`

## Non-Goals

- No renderer in Core.
- No localization system.
- No database table for templates.
- No migration for old prose fields; Wave 3 already removed `assistant_message` from goal parsing.
