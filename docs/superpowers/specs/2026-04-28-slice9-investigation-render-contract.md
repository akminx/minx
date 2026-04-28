# Slice 9 Investigation Render Contract Update

**Date:** 2026-04-28  
**Status:** Proposed amendment to [Slice 9: Agentic Investigations](2026-04-19-slice9-agentic-investigations.md)  
**Depends on:** Slice 6 retrieval/enrichment foundation, Slice 8 playbook audit pattern, [MCP Render Contract](2026-04-28-mcp-render-contract.md)

## Goal

Make Slice 9 investigations follow the render-contract boundary from day one: Core stores investigation records, trajectory digests, statuses, citations, and render events; Hermes owns the agent loop and final explanation prose.

## Boundary

Core provides:

- investigation lifecycle storage
- trajectory digest storage
- citation/reference fields
- status and budget metadata
- event/template keys and structured slots
- history and retrieval tools

Hermes provides:

- LLM tool-picking loop
- budget enforcement
- user-facing phrasing
- final answer composition
- channel-specific UX
- confirmation prompts and follow-up conversation

Core must not run the agent loop or produce the final conversational explanation.

## Investigation Events

Investigation lifecycle responses and stored events should use stable template keys:

- `investigation.started`
- `investigation.step_logged`
- `investigation.needs_confirmation`
- `investigation.completed`
- `investigation.failed`
- `investigation.cancelled`
- `investigation.budget_exhausted`

These are not final messages. They are render hints and audit labels.

## Core Response Shape

Lifecycle tools should return standard tool envelopes with render hints:

```json
{
  "investigation_id": 42,
  "response_template": "investigation.started",
  "response_slots": {
    "investigation_id": 42,
    "kind": "investigate",
    "status": "running",
    "harness": "hermes"
  }
}
```

Completion should return:

```json
{
  "investigation_id": 42,
  "response_template": "investigation.completed",
  "response_slots": {
    "investigation_id": 42,
    "kind": "investigate",
    "status": "succeeded",
    "tool_call_count": 8,
    "citation_count": 4,
    "cited_memory_count": 3,
    "cost_usd": 0.12
  }
}
```

Hermes may render those as "Investigation complete" or a richer explanation, but the final prose lives outside Core.

`complete_investigation` and `log_investigation` should accept optional `citation_refs`, stored in `citation_refs_json`, so references used by `answer_md` are available without parsing prose.

## Schema Adjustments

The existing Slice 9 spec stores `answer_md`. Under this update:

- `answer_md` is optional harness-authored output, not Core-authored prose.
- Core may store `answer_md` when Hermes passes it to `complete_investigation`, but Core must not generate or rewrite that final explanation.
- Add or reserve `answer_template` and `answer_slots_json` only if Core needs to store a structured final render hint.
- Prefer storing citations and structured answer metadata separately from final text when possible.

Default implementation additions:

```sql
response_template TEXT,
response_slots_json TEXT,
citation_refs_json TEXT
```

Where:

- `response_template` stores the latest lifecycle/event template key.
- `response_slots_json` stores JSON slots for that latest event.
- `citation_refs_json` stores references used by the harness answer. It is a JSON list of typed objects:

```json
[
  {"type": "memory", "id": 123},
  {"type": "investigation", "id": 42},
  {"type": "vault_path", "path": "Minx/Reviews/2026-04-28.md"},
  {"type": "tool_result_digest", "tool": "finance_query", "digest": "9a2f1c4e7b8d..."}
]
```

Allowed reference `type` values are `memory`, `investigation`, `vault_path`, and `tool_result_digest`. Unknown reference types should be rejected until a follow-up spec defines them.

Use these columns for the initial Core implementation so `investigation_history` and `investigation_get` have a stable latest-event surface. Step-level events still live in `trajectory_json` entries. A trajectory-only storage approach should be a deliberate later simplification, and only if history/get tools continue exposing `response_template`, `response_slots`, and step event fields without parsing prose.

## Trajectory Step Shape

`append_investigation_step` accepts a single `step_json` object with this shape. Core should validate the required fields and reject raw tool output. Optional extra scalar metadata is acceptable only when JSON-safe and non-sensitive.

```json
{
  "step": 3,
  "event_template": "investigation.step_logged",
  "event_slots": {
    "row_count": 12
  },
  "tool": "finance_query",
  "args_digest": "6f12b4c8d901...",
  "result_digest": "9a2f1c4e7b8d...",
  "latency_ms": 182
}
```

Required fields: `step`, `event_template`, `event_slots`, `tool`, `args_digest`, `result_digest`, and `latency_ms`.

Digest fields are raw lowercase SHA-256 hex strings over canonical JSON or canonical text, without a `sha256:` prefix. This matches the existing Core fingerprint helper style.

Allowed `event_slots` values are structured digests, counts, enum-like labels, ids, booleans, numbers, and short normalized strings. No raw tool output should be stored in `event_slots`; use `result_digest`, `row_count`, `byte_count`, or citation ids instead. `event_slots` may include render-relevant summaries, but it does not need to duplicate top-level `tool`, `args_digest`, `result_digest`, or `latency_ms`.

Validation rules:

- `step` must be a positive integer.
- `event_template` must be one of the investigation template keys listed above.
- `tool` must be a non-empty normalized tool name.
- `args_digest` and `result_digest` must match `[0-9a-f]{64}`.
- `latency_ms` must be a non-negative integer.
- `event_slots` must be a JSON object with at most 32 top-level keys, max nesting depth 4, and string leaves capped at 1024 UTF-8 bytes.
- The serialized `step_json` must be capped at 16 KiB.
- Reject raw-output keys such as `raw_output`, `tool_output`, `result_json`, `result_rows`, `transcript`, and `messages`.

## Confirmations

If an investigation needs user confirmation before a risky step, Core should store the event and Hermes should ask:

```json
{
  "response_template": "investigation.needs_confirmation",
  "response_slots": {
    "investigation_id": 42,
    "action": "memory_confirm",
    "risk": "promote_memory",
    "target_id": 123
  }
}
```

`append_investigation_step` returns `response_template == "investigation.needs_confirmation"` only when the appended step's `event_template` is exactly `investigation.needs_confirmation`; otherwise it returns the step's render event, normally `investigation.step_logged`. The investigation status remains `running`. Hermes renders the prompt, records the user's decision by calling the appropriate Core/domain tool, and should append a later `investigation.step_logged` step that records the decision digest. Core should not invent the confirmation wording or treat confirmation slots as authorization for a domain mutation.

## Redaction And Blocking

Core should apply deterministic local secret handling before persistence:

- Redact redactable secret-shaped values in `question`, `answer_md`, `error_message`, JSON string leaves in `context_json`, JSON string leaves in `citation_refs`, and `event_slots`.
- Block non-redactable secret-shaped values, such as private key blocks, with `INVALID_INPUT`.
- Do not scan digest fields as secrets.
- Preserve structured fields when redacting JSON leaves; do not collapse structured objects into prose.

## Testing

Core tests should cover:

- `start_investigation` returns `response_template == "investigation.started"`.
- `append_investigation_step` stores step `event_template` and JSON-safe `event_slots`.
- `complete_investigation` returns `investigation.completed`, `investigation.failed`, `investigation.cancelled`, or `investigation.budget_exhausted` based on status.
- Stored slots never include raw tool output and reject raw-output key names.
- Digest fields reject non-hex or prefixed digest strings.
- `citation_refs` accept only the typed reference schema and are exposed by history/get without prose parsing.
- `question`, `context_json`, `answer_md`, `error_message`, `citation_refs`, and `event_slots` follow the redaction/blocking policy.
- `answer_md`, if present, is accepted as harness-authored content and not generated by Core.
- history/get tools expose structured event/template data without requiring prose parsing.

Hermes tests, outside this repo, should cover:

- Hermes renders user-facing investigation messages from template keys and slots.
- Hermes composes final explanation prose.
- Hermes enforces budgets and records terminal status in Core.

## Read Surfaces

`investigation_history(kind=None, harness=None, status=None, since=None, days=30, limit=100)` should expose the latest lifecycle render event for each run:

```json
{
  "runs": [
    {
      "investigation_id": 42,
      "kind": "investigate",
      "status": "succeeded",
      "response_template": "investigation.completed",
      "response_slots": {
        "investigation_id": 42,
        "kind": "investigate",
        "status": "succeeded"
      }
    }
  ],
  "truncated": false
}
```

`investigation_get` should include the same latest `response_template` / `response_slots`, `citation_refs`, and the trajectory entries with each step's `event_template` / `event_slots`. `log_investigation` is a convenience wrapper over the same lifecycle storage rules; it should return the terminal lifecycle render hint that matches the logged status.

## Relationship To The Existing Slice 9 Spec

This spec amends, rather than replaces, `2026-04-19-slice9-agentic-investigations.md`. It supersedes that spec's minimal lifecycle response examples for `start_investigation`, `append_investigation_step`, `complete_investigation`, and `log_investigation`: those tools should return the relevant ids plus `response_template` / `response_slots` when they create or transition lifecycle state.

When implementing Slice 9:

1. Keep the existing Core/Hermes loop split.
2. Add template keys and slots to lifecycle responses.
3. Treat `answer_md` as harness-authored.
4. Keep trajectory storage digest-only.
5. Avoid Core-generated final explanations.

## Non-Goals

- No Hermes implementation in this repo.
- No prompt design for the investigation agent loop.
- No dashboard UI.
- No raw transcript storage.
- No recurring unpredictable investigation scheduler.

## Self-Review

- Core owns investigation persistence and auditability.
- Hermes owns LLM loop and final prose.
- Template keys cover started, confirmation, terminal, and step events.
- The design avoids over-storing raw tool output or raw conversation text.