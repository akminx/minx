# Slice 2.1 Phase A: Core Trust Hardening Design

**Date:** 2026-04-08
**Status:** Drafted for review
**Parent:** [2026-04-06-minx-roadmap-slices.md](2026-04-06-minx-roadmap-slices.md)

## Goal

Harden the `minx-core` review boundary so richer clients can consume `daily_review` safely without seeing sensitive freeform output by default.

This phase is the first implementation cut of `Slice 2.1: Conversational Goals + Trust Hardening`, but it intentionally scopes to Core-only trust policy work that fits this repo today.

## Threat Model

This phase defends against an MCP client that is allowed to call `daily_review` but should not automatically receive sensitive review content.

The motivating client shape is:

- a conversational adapter such as Hermes or Discord
- a third-party or future client that can retain, replay, or correlate MCP responses
- any caller that should receive a safe default view rather than the full internal review artifact

For this phase, the boundary protection goal is:

- do not expose enough MCP response detail for a client to reconstruct sensitive event facts, exact spending details, detector-specific reasoning, or user-authored goal text from the default `daily_review` response

This phase does not defend against:

- direct filesystem access to the vault
- direct database access
- a compromised local process with the same machine privileges as the user
- aggregate query abuse across many tools or broad date ranges

Those risks are real, but they are separate follow-on problems. Phase A is specifically an MCP boundary hardening slice, not full at-rest or host-level protection.

## Why This Slice Comes First

The repo already exposes a structured `daily_review` artifact through the Core MCP boundary. That makes the trust boundary a real product surface now, not a future integration detail.

If we add conversational clients before hardening that boundary, those clients will inherit raw narrative and insight text that may echo sensitive event details or otherwise expose more context than we want to share by default.

The safest sequence is:

1. make Core output policy explicit
2. return a client-safe review projection
3. build conversational adapters on top of that safer contract

## Scope

This phase includes:

- a Core-owned review output policy for MCP-facing `daily_review` responses
- a redacted structured projection derived from the full internal `DailyReview` artifact
- explicit redaction metadata in the MCP response so clients know they are consuming a protected view
- tests that verify both internal review generation and client-facing redaction behavior

This phase does not include:

- Hermes or Discord client code
- natural-language goal capture
- generalized auth or per-user permissions
- durable memory or insight expiration work
- a fully general policy engine for every future tool in the repo

## Current Baseline

Today, `daily_review` builds a `DailyReview` artifact in Core and returns most of it directly at the MCP boundary.

The current repo already has one coarse trust rule:

- timeline review output excludes non-`normal` events

That rule is useful but incomplete because the MCP response also includes freeform fields such as:

- `narrative`
- insight `summary`
- insight `supporting_signals`
- `next_day_focus`
- rendered `markdown`

Those fields are helpful for trusted local use, but they create a wider leak surface for conversational or remote clients because they may restate details drawn from sensitive facts even when the original sensitive events were filtered from the visible timeline.

## Design Principles

- Core owns interpretation and trust policy.
- Clients should consume explicit structured fields, not infer trust level from prose.
- The default protected view should be allowlist-based and coarse enough that blocked details cannot be trivially reconstructed from pass-through fields.
- Redaction should preserve usefulness where possible, but correctness of the trust boundary is more important than richness in Phase A.
- The first version should be narrow, deterministic, and easy to test.
- Internal review generation should stay intact; this phase hardens the boundary, not the detector pipeline itself.

## Proposed Architecture

Add a small review policy layer inside `minx_mcp/core/` with one responsibility: convert a full `DailyReview` artifact into an MCP-safe review projection.

The architecture becomes:

1. Core builds the full internal `DailyReview`
2. Core persists detector insights and writes the full markdown note as it does today
3. Core applies a review output policy before returning the MCP response
4. MCP clients receive the protected projection plus explicit redaction metadata

This keeps business logic and durability behavior unchanged while making the external boundary safer.

## Policy Model

Phase A uses one default boundary policy for `daily_review`.

The policy classifies output into three buckets:

- `pass_through`: safe structured fields that can be returned unchanged
- `redacted`: fields whose content should be replaced with a safer projection
- `blocked`: fields that should not be returned at all at the MCP boundary

### Pass-Through Fields

These remain visible because they are boundary metadata rather than user data:

- `date`
- `llm_enriched`
- `redaction_applied`
- `redaction_policy`
- `redacted_fields`
- `blocked_fields`

The only user-facing review content that may pass through directly is a deliberately coarse protected summary model defined in this spec. The existing internal timeline, spending, insights, goals, and markdown payloads do not pass through unchanged.

### Redacted Fields

These remain present only in coarse, policy-generated form:

- `narrative`
- `next_day_focus`
- review health/activity summaries

The replacement content should stay useful without exposing facts that a client could use to reconstruct what happened. In Phase A, this means bucketed or boolean summaries rather than exact counts or raw records.

`redacted_fields` should identify raw artifact fields whose content was replaced at the boundary. In Phase A, that means the freeform text surfaces such as `narrative` and `next_day_focus`, not every derived coarse summary field that the policy constructs from scratch.

### Blocked Fields

These are omitted from the default MCP response in Phase A:

- structured timeline entries
- structured spending snapshot
- structured open loop records
- raw goal progress records
- rendered `markdown`
- raw insight records
- insight `summary`
- insight `supporting_signals`
- `dedupe_key`
- `source`
- user-authored goal titles
- user-authored goal notes
- goal filter details such as category, merchant, and account names

These fields are either direct facts or high-signal metadata that would let a client reconstruct the redacted content too easily.

## Output Contract Changes

`daily_review` should keep returning the normal contract envelope from `wrap_async_tool_call`, but the successful `data` payload should become an explicit protected projection.

The payload should include:

- protected summary fields instead of the current raw review artifact
- redacted text fields in place of raw freeform text
- `redaction_applied: true`
- `redaction_policy: "core_default_v1"`
- a list of `redacted_fields`
- a list of `blocked_fields`

That makes the trust posture visible and stable for future clients.

### Protected Summary Shape

The default protected view should be intentionally small:

- `date`
- `llm_enriched`
- `attention_areas`
- `activity_level`
- `goal_attention_level`
- `open_loop_level`
- `narrative`
- `next_day_focus`
- `redaction_applied`
- `redaction_policy`
- `redacted_fields`
- `blocked_fields`

Suggested coarse enums for Phase A:

- `activity_level`: `none | low | moderate | high`
- `goal_attention_level`: `none | some | many`
- `open_loop_level`: `none | some | many`

`attention_areas` is an allowlisted set of coarse categories such as:

- `activity`
- `goals`
- `open_loops`
- `spending`

It should never include merchant names, category names, account names, event types, detector names, or user-authored goal text.

In Phase A, `attention_areas` is presence-based rather than exhaustive: an area appears only when the protected view has reason to surface that area as part of the coarse summary. This binary signal is an accepted trade-off in the current threat model and should be implemented with a pinned allowlist constant.

`llm_enriched` is intentionally passed through as process metadata rather than user data. It may reveal that the review used fallback logic instead of the LLM path, and that is acceptable in Phase A because the field does not materially increase reconstruction risk for user activity.

## Redaction Rules

Phase A should use deterministic rules instead of model-based rewriting.

### Narrative

Replace the raw narrative with a coarse summary built from:

- coarse activity and attention buckets
- the presence of goal attention
- the presence of open loops

The narrative should avoid:

- exact counts
- event-specific names
- merchants
- categories
- account names
- detector names
- goal titles
- quoted freeform details

### Protected Insights Decision

Phase A does not return per-insight records at the default MCP boundary.

The protected view may reflect that attention exists in the aggregate, but it should not expose:

- `insight_type`
- `dedupe_key`
- `severity`
- `actionability`
- `source`
- `confidence`

Those fields are semantically rich enough to become side channels even without the original summary text.

### Focus

Return short generic focus prompts derived from existing structure, such as:

- review outstanding items
- check active goals
- follow up on today’s flagged areas

Do not echo detailed freeform phrasing from the internal artifact.

### Goals

Phase A does not return raw goal records or user-authored goal text at the default MCP boundary.

The protected view may expose only coarse goal attention state such as:

- `goal_attention_level`
- whether goals contributed to the protected narrative or focus list

It must not expose:

- goal titles
- goal notes
- category filters
- merchant filters
- account filters
- exact target or actual values

### Spending And Timeline

Phase A does not return the existing structured timeline or spending snapshot at the default MCP boundary.

The protected view may expose only:

- a coarse `activity_level`
- the presence of `spending` within `attention_areas`

It must not expose:

- timeline timestamps beyond the requested review date
- event summaries
- merchant- or category-level spending
- exact totals
- top merchants
- top categories

## Policy Versioning

`redaction_policy` is an informational server-owned version tag, not a client negotiation mechanism.

Compatibility rules for Phase A:

- clients must treat unknown policy names as valid protected responses if `redaction_applied` is `true`
- clients must tolerate missing fields and should not assume future policy versions expose the same shape
- future versions may tighten the protected view without preserving field-for-field compatibility with `core_default_v1`

That means the stable contract in Phase A is not the exact field inventory. The stable contract is that the response is explicitly protected and that clients must consume it defensively.

## Determinism And Predictability

Phase A keeps deterministic redaction despite its predictability.

That is acceptable only because the protected view is allowlist-based and intentionally coarse. Once high-signal structured facts are blocked, predictability of the coarse template is no longer the primary leak vector.

Model-based rewriting is explicitly avoided in this phase because it is harder to test, less stable, and more likely to leak details through inconsistent paraphrase.

## Non-Goals For Phase A

This phase does not attempt to:

- redact historical vault notes
- classify sensitivity at the sentence or token level
- introduce user-selectable trust levels
- harden every MCP tool in the repo in one pass
- replace the current internal markdown note format
- solve aggregate longitudinal query leakage
- defend against callers that already have direct vault or database access

## File-Level Plan Direction

Expected implementation surfaces:

- `minx_mcp/core/review.py`
  Keep internal artifact generation intact.
- `minx_mcp/core/server.py`
  Apply the protected projection before returning the `daily_review` MCP payload.
- `minx_mcp/core/models.py`
  Add any new dataclasses needed for the protected review projection.
- `tests/test_review.py`
  Cover the projection and redaction rules.
- `tests/test_core_server.py`
  Verify the `daily_review` tool returns the protected contract shape.

If the projection logic grows beyond a few helpers, it may live in a new focused module such as `minx_mcp/core/review_policy.py`.

## Testing Strategy

Use TDD throughout.

Required test coverage:

- a review-policy test proving raw narrative is replaced with coarse text
- a review-policy test proving raw timeline and spending structures are blocked from the client projection
- a review-policy test proving goal titles and goal notes are not exposed in the client projection
- a review-policy test proving per-insight fields such as `dedupe_key` and `source` are not exposed
- a review-policy test proving exact counts are converted to coarse buckets
- a review-policy test proving `attention_areas` values stay within the pinned allowlist
- a review-policy test proving markdown is blocked at the MCP boundary
- a server test proving `daily_review` returns `redaction_applied`, `redaction_policy`, and `redacted_fields`
- a server test proving `daily_review` returns `blocked_fields` and the protected summary shape instead of the raw artifact fields
- a regression test proving internal review generation still produces the full artifact used for vault output

## Risks And Trade-Offs

The main trade-off is that the MCP response becomes less expressive for trusted local clients. That is acceptable in this phase because the immediate goal is to create a safe default contract for future conversational surfaces.

There is also a risk of over-redacting and making the review feel bland. We accept that in Phase A because a bland safe default is better than a rich but leaky one.

Another deliberate trade-off is that this phase does not solve at-rest leakage or aggregate query abuse. Those remain follow-on trust slices and should not be implied as solved by this boundary-only work.

## Success Criteria

This phase is successful when:

- `daily_review` no longer returns raw markdown at the MCP boundary
- freeform response fields are redacted deterministically
- raw structured timeline, spending, goal, and insight data are blocked from the default client view
- user-authored goal text is not exposed in the default client view
- exact counts are not exposed where they materially increase reconstruction risk
- the response advertises that redaction was applied
- internal review generation and vault persistence still work
- the new behavior is covered by automated tests

## Follow-On Work

Once this phase is landed, the next Slice 2.1 step can add a thin conversational goal adapter over the safer Core contract instead of binding clients directly to raw review prose.
