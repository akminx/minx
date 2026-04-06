# Minx MCP Response Contracts Design

**Date:** 2026-04-05
**Status:** Approved for planning
**Scope:** Standardize MCP tool responses and error handling for the current finance domain and future Minx domains

## Goal

Define one strict response envelope for every MCP tool in `minx-mcp` so all current and future domains return a consistent shape for success and failure.

## Success Criteria

This design is successful when:

- every MCP tool returns the same top-level envelope
- success responses always place tool output inside `data`
- failure responses always set `success` to `false`, `data` to `null`, and provide a stable `error_code`
- finance becomes the reference implementation for future domains
- future `health` and `meals` domains can reuse the same helpers without inventing their own response rules

## Non-Goals

This design does not include:

- adding new finance features
- changing finance business behavior beyond response formatting and error classification
- implementing `health` or `meals`
- designing Hermes-specific adapters
- redesigning the transport layer

## Response Contract

Every MCP tool must return exactly this JSON-compatible structure:

```json
{
  "success": true,
  "data": {},
  "error": null,
  "error_code": null
}
```

On failure, tools must return:

```json
{
  "success": false,
  "data": null,
  "error": "human-readable message",
  "error_code": "INVALID_INPUT"
}
```

### Field Rules

- `success`
  - Required boolean.
  - `true` for successful operations.
  - `false` for all failures.

- `data`
  - Required.
  - Holds the successful tool payload.
  - May be any JSON-serializable value.
  - Pagination, counts, summaries, cursors, and other tool-specific metadata must live inside `data`, not at the envelope level.
  - Must be `null` on failure.

- `error`
  - Required.
  - Must be `null` on success.
  - Must be a short human-readable message on failure.

- `error_code`
  - Required.
  - Must be `null` on success.
  - Must be a stable machine-friendly string on failure.

## Error Code Set

The initial shared error taxonomy is intentionally small:

- `INVALID_INPUT`
  - The caller supplied invalid or missing arguments.
  - Examples: invalid date, empty string, unsupported source kind, invalid transaction id list.

- `NOT_FOUND`
  - The requested resource does not exist.
  - Examples: unknown account, unknown category, unknown transaction id, unknown job id.

- `CONFLICT`
  - The request is valid but conflicts with current state or a uniqueness constraint.
  - Examples: duplicate rule creation or other future constraint conflicts.
  - This code is defined now as part of the shared taxonomy but should remain unused until a concrete state-conflict case exists.

- `INTERNAL_ERROR`
  - An unexpected failure occurred that was not intentionally classified.
  - Examples: unexpected parser failure, unhandled database exception, unexpected filesystem error.

## Module Design

Add a shared `minx_mcp/contracts.py` module that owns:

- error code constants
- a small typed exception hierarchy for classified failures
- helpers for building success and failure envelopes
- one server-facing wrapper helper that converts tool callables into compliant responses

New error codes must be added in this shared module and become part of the shared cross-domain contract. Domains must not invent private top-level error codes independently.

The core helpers should be simple and reusable by every domain.

## Exception Strategy

The service and server layers should stop relying on raw `ValueError` for all intentional failures.

Instead:

- use typed contract exceptions for expected failures that should map to a known `error_code`
- reserve uncaught exceptions for true unexpected failures

Recommended initial exception classes:

- `MinxContractError`
- `InvalidInputError`
- `NotFoundError`
- `ConflictError`

`MinxContractError` should carry:

- the human-readable message
- the stable error code

## Validation Ownership

Validation should be split by responsibility so the code avoids duplication without making the service layer unsafe to call directly.

### Server Boundary Validation

`server.py` should validate transport-facing argument shape and obvious malformed input before invoking domain services.

Examples:

- required string arguments are not empty
- integer lists are present and contain valid positive ids
- date strings are syntactically valid ISO dates when the tool contract requires them
- enum-like arguments such as `source_kind` or `match_kind` are supported
- file references point to an existing file when that is a transport-facing precondition

### Service Validation

`service.py` should own domain validation, state validation, and business invariants.

Examples:

- referenced accounts, categories, transactions, or jobs exist
- import paths are inside the allowed import root
- report windows satisfy weekly or monthly business rules
- requests do not violate domain constraints or conflict with current state

The service layer should not blindly trust the server layer, but duplicate checks should be removed where the same validation currently exists in both places. The split should be: server validates request shape, service validates domain meaning.

## Finance Integration

Finance is the first domain converted and becomes the reference pattern.

### Server Layer

`minx_mcp/finance/server.py` should:

- stop exposing raw exceptions to MCP callers
- wrap every tool with the shared contract helper
- always return the strict envelope
- perform transport-facing argument validation before calling services

The shared wrapper must be catch-all:

- catch `MinxContractError` and emit the corresponding classified failure envelope
- catch any other `Exception` and emit an `INTERNAL_ERROR` envelope
- log unexpected exceptions with traceback before returning `INTERNAL_ERROR`
- never allow a raw exception traceback to escape through the MCP tool boundary

### Service Layer

`minx_mcp/finance/service.py` should:

- keep business logic and DB operations largely intact
- replace generic `ValueError` with typed contract errors where error classification matters
- classify known lookup failures as `NOT_FOUND`
- classify validation failures as `INVALID_INPUT`
- leave truly unexpected failures to be wrapped as `INTERNAL_ERROR`

### Initial Classification Guidance

Use `INVALID_INPUT` for:

- malformed dates
- unsupported transport-facing arguments
- empty required strings
- invalid list contents
- import paths outside the allowed root

Use `NOT_FOUND` for:

- unknown account names
- unknown category names
- unknown transaction ids
- unknown job ids

Use `CONFLICT` only where a real state conflict or uniqueness issue is detected.

### Tool-Specific Clarification

`finance_job_status` should no longer return a successful `None` payload for a missing job id.

If the requested job does not exist, the tool should return:

- `success: false`
- `data: null`
- `error`: a human-readable not-found message
- `error_code: "NOT_FOUND"`

### Data Ownership Clarification

This pass standardizes the top-level envelope, not the exact layer that constructs successful payload contents.

It is acceptable for:

- some tools to return service-owned payloads directly
- some tools to shape their `data` payload in the server layer after calling a service

The requirement is that the final MCP response always uses the shared contract envelope.

## Testing Strategy

Update finance tests so they verify:

- successful tools return the full envelope
- classified failures return the correct `error_code`
- unexpected failures return `INTERNAL_ERROR`
- no finance tool returns a non-envelope response

The tests should validate both:

- success examples for `safe_finance_summary`, `finance_categorize`, and `finance_job_status`
- at least one failure example per supported error category
- one unexpected-failure path that confirms the caller sees `INTERNAL_ERROR`

## Rollout Plan

Implement in this order:

1. Add shared contract helpers and exception types.
2. Apply the validation split in finance so server-only shape checks and service-only domain checks are clearly separated and duplicate checks are removed.
3. Update finance server wrappers to always emit the envelope and log unexpected exceptions before returning `INTERNAL_ERROR`.
4. Replace key finance `ValueError` cases with typed contract errors.
5. Update tests to assert the new response contract for `safe_finance_summary`, `finance_categorize`, and `finance_job_status`.
6. Update existing `ValueError`-based test assertions to use the typed contract errors or envelope expectations, depending on the layer under test.
7. Use the finance implementation as the template for future domains.

## Design Notes

- This is the right time to make the contract strict because there is only one consumer.
- A strict envelope is more valuable than preserving ad hoc payloads because future domains will benefit from a single pattern.
- This design intentionally keeps the error code set small so it remains easy to use consistently.
