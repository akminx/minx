# Generic Memory Capture (Core MCP)

**Status:** Approved design - not yet implemented  
**Depends on:** Slice 6 memory foundation (`MemoryService`, FTS5, fingerprints, secret scanning, enrichment/embedding hooks, vault projection)  
**Date:** 2026-04-27

## Goal

Add a single generic capture entrypoint so users and agents can drop quick, reviewable text into the durable memory store without hand-authoring a strict `memory_create` envelope. Captures reuse the existing `MemoryService.create_memory` write path so secret scanning, payload validation, content fingerprinting, lifecycle events, FTS maintenance, and later explicit embedding enqueue behave like other memory writes.

This is OB1-inspired: fast capture, triage later. It is not an OB1 clone: one tool, local-first, no capture-time intelligence beyond deterministic normalization.

## Non-Goals

- No second tool such as `memory_capture_active`; high-confidence, structured writes stay on `memory_create`.
- No LLM classification, summarization, or routing during capture; no outbound network calls on the capture path.
- No automatic embedding or enrichment enqueue from capture alone; callers use existing `memory_embedding_enqueue` when they want the embedding pipeline.
- No change to proposal/merge semantics in `ingest_proposals`; capture is direct `create_memory`, not the proposal batch path.
- No requirement to replicate OB1 schemas, folders, or sync models.

## API Shape

Tool name: `memory_capture`

Conceptual parameters:

- `text: str` (required): primary capture body; non-empty after trim.
- `capture_type: str = "observation"`: caller-defined label, normalized as described below.
- `scope: str = "core"`: same semantics as `memory_create`.
- `subject: str | None = None`: caller override; if omitted, derived from `capture_type` and `text`.
- `source: str = "user:capture"`: stored in the `source` column and scanned like other memory text.
- `confidence: float = 0.5`: must satisfy `0.0 <= confidence < 0.8`; captures are always reviewable candidates.
- `metadata: object | None = None`: optional JSON object stored under `payload.metadata` after validation.

Successful calls use the standard tool response envelope. The inner `data` payload contains:

```json
{
  "memory": "<MemoryRecord dict>",
  "response_template": "memory_capture.created_candidate",
  "response_slots": {
    "memory_id": 123,
    "status": "candidate",
    "memory_type": "captured_thought",
    "scope": "core",
    "subject": "observation:Buy milk tomorrow",
    "capture_type": "observation"
  }
}
```

`response_template` and `response_slots` are render hints for Hermes or another harness. Core must not return model-authored conversational copy for capture success. The template key lets Hermes decide whether to say "I saved that for review," "I added it to memory candidates," or any other user-facing phrasing in the active harness voice.

Implementation home: `minx_mcp/core/tools/memory.py`, delegating to `MemoryService.create_memory` in `minx_mcp/core/memory_service.py`.

## Core / Hermes Render Contract

This feature follows the project-wide boundary for conversational MCP responses:

- Core may use deterministic logic and, in other tools, LLMs for structured interpretation: intents, proposals, plans, classifications, template keys, and slots.
- Core must not rely on model-authored final user-facing prose.
- Core stores durable facts, memory rows, lifecycle events, audit records, and search indexes.
- Hermes owns final wording, personality, channel-specific UX, timing, confirmations, and follow-up conversation.
- Conversational MCP responses should prefer stable template keys and structured slots over prose. Legacy prose-like fields such as `question` or `assistant_message` may exist elsewhere as compatibility fallbacks, but new capture behavior should expose render hints directly.

For `memory_capture`, that means Core returns `response_template` and `response_slots`. Hermes can render those however it wants:

```json
{
  "response_template": "memory_capture.created_candidate",
  "response_slots": {
    "memory_id": 123,
    "status": "candidate",
    "subject": "observation:Buy milk tomorrow",
    "capture_type": "observation"
  }
}
```

Hermes might turn that into "Saved for memory review," "I added that as a candidate memory," or another phrase. That phrasing is intentionally outside Core.

## Data Model

Use a dedicated, permissive memory type: `captured_thought`.

Do not register `captured_thought` in `PAYLOAD_MODELS` in `minx_mcp/core/memory_payloads.py`. Unknown-type permissive validation should apply, keeping existing strict `memory_create` types unchanged. A loose Pydantic model with `extra="allow"` can be considered later, but is not required for this slice.

Canonical payload:

```json
{
  "text": "<primary body>",
  "capture_type": "<normalized capture_type>",
  "metadata": {}
}
```

Rules:

- `text` is required in the stored payload and equals the post-validation body.
- `capture_type` is always stored.
- `metadata` is omitted when the argument is null or empty after normalization.
- If present, `metadata` must be a JSON object. Enforce max nesting depth 4, max 32 top-level keys, and string leaf values <= 4 KiB UTF-8 each. Violations raise `InvalidInputError`.

For unknown types, `_memory_fingerprint_input` already uses the full canonical JSON payload as the fingerprint value part. `captured_thought` therefore dedupes on identical `(memory_type, scope, subject, payload)`, which is appropriate for verbatim capture.

## FTS5 Search

Current FTS trigger/rebuild logic includes `$.value`, `$.note`, `$.signal`, `$.limit_value`, and `$.aliases`, but not `$.text`. For captures to be searchable by body, add a new migration that drops and recreates the FTS triggers, following `025_memory_fts_aliases.sql`, and update `scripts/rebuild_memory_fts.py`.

Append at least:

```sql
COALESCE(json_extract(payload_json, '$.text'), '') || ' ' ||
COALESCE(json_extract(payload_json, '$.capture_type'), '')
```

Do not blindly flatten arbitrary `metadata` into FTS. Searches over metadata can wait for a dedicated design.

Adding `$.text` and `$.capture_type` to the generic memory FTS extraction means any future memory type that uses those canonical payload keys will also be indexed. That is intentional; the extraction is key-based, not limited to `captured_thought`.

Existing databases: operators run `python -m scripts.rebuild_memory_fts` after migration so historical `captured_thought` rows, if any exist, pick up the new extraction.

## Vault / Obsidian

If vault projection uses type-specific templates, `captured_thought` should render as a readable note with title from `subject`, body from `payload.text`, and optional `capture_type`. Falling back to a generic memory-note layout is acceptable so unknown layouts do not skip rows.

## Lifecycle

- `MemoryService.create_memory` sets `candidate` when `confidence < 0.8` and `active` when `confidence >= 0.8`.
- `memory_capture` validates `confidence < 0.8` before calling the service, so captures are always candidate/review-first.
- Default `confidence=0.5` yields `candidate`.
- `memory_confirm`, `memory_reject`, `memory_expire`, and `get_pending_memory_candidates` apply unchanged.
- `memory_search` and `memory_hybrid_search` default to `status="active"`, so reviewers must pass `status="candidate"` or `status=None` to surface captures before confirmation.
- Hermes should render any capture acknowledgement from `response_template` and `response_slots`, not from Core prose.

## Security

- Capture uses the same memory gate as `create_memory`: `scan_memory_input` before and after payload validation.
- BLOCK verdicts raise the same secret-detected contract as other memory writes.
- REDACTED fields store redacted values and audit-style event payloads per existing behavior.
- Metadata values participate in scanning like the rest of the payload.
- No new exfiltration path is introduced: capture remains local SQLite plus MCP.

## Deterministic Normalization

Subject derivation when `subject` is omitted:

1. Derive the subject fragment from raw `text`, before body-wide whitespace collapsing.
2. Split raw `text` into lines and use the first non-empty line after trimming. Within that line, collapse runs of whitespace to a single space.
3. If no non-empty line exists, use the literal `capture`.
4. Prefix with normalized `capture_type`: `{capture_type}:{fragment}`.
5. Bound total UTF-8 length to 200 bytes; if truncation occurs, append `...` within the limit.

When `subject` is supplied, trim it, reject empty, and apply the same 200-byte bound.

Stored `payload.text` is normalized separately: strip leading/trailing whitespace and collapse internal whitespace across the full body to a single space.

`capture_type` normalization:

- Blank becomes `observation`.
- Lowercase ASCII letters and replace interior whitespace runs with `_`.
- Allowed characters are letters, digits, `_`, and `-`; other contiguous runs become one `_`.
- Collapse duplicate `_`, strip leading/trailing `_`, and bound to 64 UTF-8 bytes with `...` on truncation.

## Error Handling

- Empty `text`: `InvalidInputError`.
- Invalid `metadata`: `InvalidInputError`.
- `confidence >= 0.8`: `InvalidInputError`; callers should use `memory_create` for explicit active memories.
- Secret BLOCK verdict: same secret-detected contract as `memory_create`.
- Empty `scope`, `source`, or invalid confidence type/range: existing validators raise `InvalidInputError`.
- DB conflicts: existing `ConflictError` or service errors.

No partial writes: transaction behavior matches `create_memory`.

## Tests

- `tests/test_core_memory_tools.py`: `memory_capture` happy path, default candidate status, `response_template` / `response_slots`, optional subject, low confidence, metadata, high-confidence rejection, secret blocking, invalid metadata, and derived subject stability.
- `tests/test_memory_service.py`: pure helper tests if helpers live outside the MCP tool module; service round-trip for a `captured_thought` row through `list_memories` and `search_memories(status="candidate")`.
- `tests/test_rebuild_memory_fts.py`: captured thought text is searchable after rebuild.
- Tests must be offline, with no API keys or embedding calls.

## Documentation

- `README.md`: add a short "Quick capture vs structured create" note explaining `memory_capture`, `memory_create`, candidate review, and `memory_confirm`.
- `HANDOFF.md`: note the new FTS migration and rebuild step.
- MCP tool docstring: state defaults, review workflow, render-template contract, and that search defaults to active memories.

## Implementation Steps

1. Add migration `026_memory_capture_fts.sql` to refresh FTS triggers with `$.text` and `$.capture_type`.
2. Update `scripts/rebuild_memory_fts.py` to match trigger logic.
3. Add pure capture helpers, likely in `minx_mcp/core/memory_capture.py`.
4. Register and implement `memory_capture` in `minx_mcp/core/tools/memory.py`.
5. Confirm no special vault projection handling is required; add a follow-up only if current generic memory projection rejects `captured_thought`.
6. Update README and HANDOFF.
7. Run targeted tests, full tests, ruff, mypy, and `git diff --check`.

## Self-Review

- Single tool only; `memory_create` remains the structured/active path.
- Capture is constrained to candidate confidence.
- No LLM, no auto-embedding, no metadata in FTS.
- Core returns render hints; Hermes owns final capture acknowledgement prose.
- Data shape, lifecycle, FTS, security, errors, tests, docs, and rollout steps are specified without reserved placeholders.

