# Goal Parse Render Contract Migration

**Date:** 2026-04-28  
**Status:** Proposed follow-up  
**Depends on:** `goal_parse`, `GoalCaptureResult`, existing goal CRUD tools, [MCP Render Contract](2026-04-28-mcp-render-contract.md)

## Goal

Update `goal_parse` so it keeps using Core for structured goal interpretation while returning template keys and slots that Hermes can render in its own voice.

This preserves the existing architecture: Core owns goal facts, goal validation, and natural-language-to-structured-proposal interpretation; Hermes owns conversation flow and final wording.

## Current Problem

`goal_parse` and its lower-level `GoalCaptureResult` still expose prose-like fields:

- `assistant_message`
- `question`
- `options`

Those fields are deterministic today, not model-authored final prose, but they still encourage clients to display Core-authored wording directly. They should remain as compatibility fallbacks while new callers prefer template/slot fields.

## Desired Response Shape

For create/update/no-match style outcomes:

```json
{
  "result_type": "create",
  "action": "goal_create",
  "payload": {
    "goal_type": "spending_cap",
    "metric_type": "sum_below",
    "domain": "finance",
    "title": "Dining Out under $250",
    "target_value": 25000,
    "period": "monthly"
  },
  "response_template": "goal_parse.create.ready",
  "response_slots": {
    "action": "goal_create",
    "goal_type": "spending_cap",
    "subject": "Dining Out",
    "period": "monthly",
    "target_value": 25000
  },
  "assistant_message": "I can create Dining Out Spending Cap."
}
```

For clarification outcomes:

```json
{
  "result_type": "clarify",
  "clarification_type": "ambiguous_goal",
  "clarification_template": "goal_parse.clarify.ambiguous_goal",
  "clarification_slots": {
    "action": "goal_update",
    "field": "goal_id",
    "candidate_count": 2
  },
  "options": [
    { "kind": "goal", "goal_id": 1, "label": "Dining Out under $250" }
  ],
  "resume_payload": {
    "target_value": 25000
  },
  "question": "Which goal do you mean?"
}
```

Hermes should render from `response_template` / `response_slots` or `clarification_template` / `clarification_slots`. `assistant_message` and `question` remain deterministic fallback fields for old clients.

## Template Keys

Recommended keys:

- `goal_parse.create.ready`
- `goal_parse.update.ready`
- `goal_parse.no_match.unsupported`
- `goal_parse.clarify.missing_target`
- `goal_parse.clarify.ambiguous_goal`
- `goal_parse.clarify.ambiguous_subject`
- `goal_parse.clarify.missing_goal`
- `goal_parse.clarify.vague_intent`

Keys should map one-to-one with semantic outcomes, not with final wording.

## Slot Rules

Slots should include only structured data:

- `action`
- `goal_id`
- `goal_type`
- `subject_kind`
- `subject`
- `period`
- `target_value`
- `status`
- `field`
- `candidate_count`

Do not place complete user-facing sentences in slots.

Do not duplicate the full top-level `payload` in `response_slots` by default. The top-level `payload` remains the authoritative machine proposal; slots should be a small projection of the normalized values Hermes needs to choose a template rendering, not a second copy of the proposal. If a future template truly needs a preview of a larger proposal, add an explicit small field such as `payload_summary` instead of copying the full payload object.

## Implementation Shape

Likely touched files:

- `minx_mcp/core/goal_models.py`
- `minx_mcp/core/tools/goals.py`
- `minx_mcp/core/goal_capture_nl.py`
- `minx_mcp/core/goal_capture_llm.py`
- `minx_mcp/core/goal_parse.py`
- `tests/test_goal_capture.py`
- `tests/test_core_goal_tools.py` or the nearest existing Core goal tool tests

Recommended model changes:

- Add `response_template: str | None`
- Add `response_slots: dict[str, object] | None`
- Add `clarification_template: str | None`
- Add `clarification_slots: dict[str, object] | None`

Validation:

- This migration is additive. Existing compatibility fields remain required where current validation requires them: `assistant_message` for `create`, `update`, and `no_match`; `question` for `clarify`.
- `create`, `update`, and `no_match` require `response_template` and `response_slots`, and must omit `clarification_template` and `clarification_slots`.
- `clarify` requires `clarification_template` and `clarification_slots`, and must omit `response_template` and `response_slots`.
- Existing `options` and `resume_payload` validation remains unchanged: `ambiguous_goal` and `ambiguous_subject` require both non-empty `options` and `resume_payload`; `missing_goal` omits `options`.

Allowed render fields by result type:

| `result_type` | Required render fields | Must omit | Compatibility fields |
|---|---|---|---|
| `create` | `response_template`, `response_slots` | `clarification_template`, `clarification_slots` | `assistant_message` |
| `update` | `response_template`, `response_slots` | `clarification_template`, `clarification_slots` | `assistant_message` |
| `no_match` | `response_template`, `response_slots` | `clarification_template`, `clarification_slots` | `assistant_message` |
| `clarify` | `clarification_template`, `clarification_slots` | `response_template`, `response_slots` | `question`, subtype-specific `options` / `resume_payload` |

Clarification subtype compatibility rules:

| `clarification_type` | `options` | `resume_payload` |
|---|---|---|
| `ambiguous_goal` | required, non-empty | required |
| `ambiguous_subject` | required, non-empty | required |
| `missing_goal` | omitted | optional continuation context |
| `missing_target` | omitted | optional continuation context |
| `vague_intent` | omitted | optional, usually omitted |

Serialization:

- Extend `minx_mcp/core/tools/goals.py::_goal_parse_result_to_dict` to include the new template and slot fields.
- Keep existing serialized fields (`assistant_message`, `question`, `options`, `resume_payload`) while old clients migrate.
- Ensure preferred render fields are emitted for every result, even when the compatibility field has the same semantic outcome.

## LLM Boundary

The LLM may still produce structured interpretation fields such as intent, subject kind, period, target value, update kind, and goal id. It must not be trusted as the source of final user-facing wording.

If the LLM path returns prose-like fields, normalize them into template keys and slots before returning a tool response. Tests should include a stub LLM that returns personality-heavy wording and assert that wording is not surfaced in template fields or slots.

## Testing

Add or update tests to cover:

- create result includes `response_template == "goal_parse.create.ready"`
- update result includes `response_template == "goal_parse.update.ready"`
- no-match result includes `response_template == "goal_parse.no_match.unsupported"`
- clarify result includes `clarification_template` based on `clarification_type`
- ambiguous goal/subject clarify results preserve `resume_payload` and options
- slots contain structured payload values needed by Hermes
- compatibility fields still exist and are deterministic
- model-authored prose from an LLM stub is not exposed as preferred render data

Run:

```bash
python -m pytest tests/test_goal_capture.py -v
python -m pytest tests/test_core_goal_tools.py -k "goal_parse" -v
python -m ruff check minx_mcp/core tests/test_goal_capture.py
python -m mypy minx_mcp
```

Adjust exact test file names to match current repo layout when implementing.

## Non-Goals

- No Hermes renderer in this repo.
- No removal of `assistant_message` or `question` in this migration.
- No expansion of supported goal language.
- No changes to `goal_create` or `goal_update` persistence semantics.

## Rollout

1. Add template/slot fields to models and tool output.
2. Keep existing fields for compatibility.
3. Update Hermes to prefer template/slot fields.
4. Later, after Hermes and other clients migrate, consider deprecating direct use of `assistant_message` and `question`.

## Self-Review

- Scope is limited to render contract migration for `goal_parse`.
- Core still owns structured interpretation, not final prose.
- Hermes remains responsible for user-facing wording.
- Existing clients are not forced to migrate immediately.
