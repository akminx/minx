# MCP Render Contract

**Date:** 2026-04-28  
**Status:** Proposed project-wide guardrail  
**Depends on:** Existing MCP tool response contracts, Hermes as the primary harness, Slice 2.5 Core/Harness split

## Goal

Define the project-wide rule for responses that a harness might render to the user: Core and domain MCPs return structured data plus stable render hints; Hermes or another harness owns the final wording, personality, channel-specific UX, timing, and follow-up conversation.

This prevents Core from drifting back into assistant prose while still allowing Core to use LLMs for structured interpretation.

## Rule

- Core may use deterministic logic or LLMs to produce structured interpretation: intents, proposals, classifications, plans, template keys, slots, and validated filters.
- Core must not rely on model-authored final user-facing prose.
- Hermes owns final prose, voice, confirmation flows, reminders, channel formatting, and conversational timing.
- Durable facts, memory rows, goal state, finance/meals/training state, audit logs, playbook runs, and investigation records remain in Minx-owned stores.
- Pure data tools do not need render hints.

## When To Use Render Hints

Use render hints when a tool response is conversational or action-oriented:

- clarifications: "which date range?", "which goal?", "which merchant?"
- acknowledgements: "saved for review", "goal proposal ready"
- confirmation prompts: risky action, destructive action, high-confidence memory promotion
- investigation state: started, needs confirmation, completed, failed
- review surfaces where Hermes needs to decide how to present next steps

Do not add render hints to pure data/read tools unless they are asking the user to choose or confirm something:

- `memory_search`
- `memory_list`
- `goal_get`
- `goal_list`
- `get_daily_snapshot`
- meals/training CRUD
- raw report/detail queries

## Response Shape

For action or acknowledgement responses, prefer:

```json
{
  "response_template": "memory_capture.created_candidate",
  "response_slots": {
    "memory_id": 123,
    "status": "candidate",
    "subject": "observation:Buy milk"
  }
}
```

For clarification responses, prefer:

```json
{
  "clarification_type": "missing_date_range",
  "clarification_template": "finance_query.clarify.missing_date_range",
  "clarification_slots": {
    "intent": "sum_spending",
    "field": "date_range",
    "filters": {}
  },
  "options": null
}
```

Compatibility fields such as `question` may remain temporarily for old callers. Do not add or revive `assistant_message`; goal parsing now returns render templates/slots only and Hermes owns acknowledgement prose.

## Template Names

Template keys should be stable, namespaced, and event-like:

- `finance_query.clarify.missing_date_range`
- `goal_parse.create.ready`
- `goal_parse.clarify.ambiguous_goal`
- `memory_capture.created_candidate`
- `investigation.started`
- `investigation.step_logged`
- `investigation.needs_confirmation`
- `investigation.completed`
- `investigation.failed`
- `investigation.cancelled`
- `investigation.budget_exhausted`

Template keys are contracts. Avoid changing them casually; add new keys when behavior meaningfully changes.

## Shared Helper / Model

Add a tiny shared helper only when at least two tools need it. Do not build a framework.

Recommended minimal shape:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderHint:
    template: str
    slots: dict[str, object]

    def as_response_fields(self, *, prefix: str = "response") -> dict[str, object]:
        return {
            f"{prefix}_template": self.template,
            f"{prefix}_slots": self.slots,
        }
```

Clarification responses should call this helper with `prefix="clarification"` or construct the `clarification_template` / `clarification_slots` fields directly. Do not emit `response_template` for clarification-only outcomes.

Expected homes, in order of preference:

1. `minx_mcp/contracts.py` if multiple MCP packages use it.
2. `minx_mcp/core/rendering.py` if only Core tools use it first.
3. Local helper functions if only one tool needs it.

Slot values must be JSON-serializable and should be identifiers, enum values, normalized labels, or structured values the harness can render. Do not put model-authored prose in slots.

## Current Application

Already aligned or being aligned:

- `finance_query`: LLM can classify intent/filter/clarification type; response should expose `clarification_template` and `clarification_slots`; old `question` remains fallback.
- `memory_capture`: spec requires `response_template` and `response_slots`; Hermes renders acknowledgement.

Needs follow-up:

- `goal_parse`: add template/slots for create, update, clarify, and no-match outcomes; keep `assistant_message` / `question` as compatibility fallback.
- Slice 9 investigations: design investigation lifecycle around structured event templates from the start.
- Legacy Core review LLM paths: mark old narrative-producing review code as legacy/internal or remove it after confirming no active MCP surface depends on it.

## Legacy Narrative Path

`LLMReviewResult.narrative` and old review-prompt code are a drift risk because they encode Core-authored final prose. They are not the active `get_daily_snapshot` boundary, but future work should not revive them as a user-facing MCP output.

Acceptable options:

1. Remove the legacy review LLM path if no active code needs it.
2. Rename/comment it as legacy/internal only.
3. If a future tool needs review synthesis, return structured sections and render hints, not final prose.

## Testing

For every conversational MCP response:

- Assert template fields exist.
- Assert slots contain the expected structured values.
- Assert model-authored wording from test LLM payloads is not surfaced in template fields or slots.
- Assert old compatibility fields remain deterministic when kept; if fallback strings remain, they may contain deterministic Core wording but must not pass through model-authored prose.

For pure data tools:

- Do not add render-hint tests unless the tool asks a user-facing question or confirmation.

## Non-Goals

- No Hermes template renderer is implemented in this repo.
- No full localization framework.
- No requirement to retrofit every MCP tool.
- No ban on Core using LLMs for structured interpretation.
- No ban on deterministic fallback strings for compatibility while clients migrate.

## Self-Review

- The contract preserves the Minx/Hermes split.
- It avoids over-applying templates to pure data tools.
- It gives future specs a concrete response shape without introducing a large framework.
- It covers finance query, memory capture, goal parse, Slice 9 investigations, and legacy narrative drift.
