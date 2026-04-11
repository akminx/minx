**Status: Implemented (historical).** This spec was implemented in an earlier slice. The codebase has since evolved — see [HANDOFF.md](/Users/akmini/Documents/minx-mcp/HANDOFF.md) for current state.

# Slice 2.1 Phase B: Conversational Goals Design

**Date:** 2026-04-08
**Status:** Implemented for the repo-scoped Core/harness-agnostic work
**Parent:** [2026-04-06-minx-roadmap-slices.md](2026-04-06-minx-roadmap-slices.md)

## Goal

Define and document the repo-scoped portion of `Slice 2.1`: a transport-agnostic conversational goal interpretation surface in `minx-core`, while keeping actual harness instance setup outside this repo.

This phase assumes Phase A trust hardening exists already:

- `daily_review` returns a protected client-facing projection by default
- Core remains the owner of goal facts, review interpretation, and trust policy

## Why This Belongs In Core

The project architecture already established the intended split:

- Core owns interpretation and goal logic
- harnesses own conversation style and rendering

That means the repo-side job is not Discord-specific wiring. The repo-side job is a reusable interpretation layer that can accept conversational goal language and turn it into a stable Core action proposal.

If we put that logic directly in Hermes, we would recreate the same business rules outside Core:

- how to tell create from update intent
- when to ask clarifying questions
- how to map natural language into valid `goal_create` or `goal_update` payloads
- how to preserve the same validation contract as the structured goal tools

That would weaken the architecture and make future clients repeat the same work.

## Where Actual Hermes Wiring Should Live

Actual Hermes or Discord integration should live outside this repo as a sibling harness integration layer over `minx-core`.

That external layer should own:

- session state
- user-facing conversation flow
- rendering for Discord or chat
- retries and confirmations at the chat UX layer
- tool orchestration across `goal_capture`, `goal_create`, `goal_update`, and `daily_review`

This repo should own only the reusable interpretation boundary that Hermes calls.

## Scope

This phase includes:

- a new Core MCP tool for conversational goal interpretation
- a policy layer that maps user text into a typed action proposal
- deterministic clarification behavior when required goal fields are missing
- end-to-end repo tests proving conversational input can lead to a real goal mutation and later protected review output
- docs that make the Hermes boundary explicit

This phase does not include:

- actual Hermes or Discord client code
- Discord message formatting
- long-lived chat session state
- freeform conversational execution that mutates goals directly without a structured action step
- generalized natural-language parsing for every future domain

## Proposed Tool: `goal_capture`

Core exposes a Core MCP tool:

- `goal_capture`

Its responsibility is to interpret a conversational goal utterance and return a typed action proposal for a harness to execute explicitly.

### Why A Separate Tool

`goal_create` and `goal_update` should remain strict structured tools.

`goal_capture` exists to bridge natural language into those tools without putting natural-language parsing inside each mutation tool and without forcing harnesses to invent their own prompt policies.

## Input Shape

Phase B should keep the input intentionally small:

- `message`: required natural-language goal utterance
- `review_date`: optional ISO date used for relative-time interpretation if needed

Phase B should apply a hard input limit:

- `message` must be non-empty after trimming
- `message` must be at most 500 characters

If the caller supplies an invalid `review_date` or an overlong/empty `message`, `goal_capture` should fail through the normal MCP contract error path with `INVALID_INPUT`.

Example inputs:

- `"Make a goal to spend less than $250 on dining out this month"`
- `"Pause my dining out goal"`
- `"Change my groceries goal to $400"`
- `"I want to spend less than $60 at Cafe this week"`

`review_date` exists only to anchor relative phrases such as:

- `"this month"`
- `"this week"`
- `"today"`

If omitted, relative-time interpretation should default to the machine-local current date, consistent with the current Core date posture.

## Output Shape

`goal_capture` should return one of four outcomes:

### 1. `create`

The message was interpreted as a new goal proposal and all required fields are available.

The response includes:

- `result_type: "create"`
- `action`: `"goal_create"`
- `payload`: a structured payload suitable for the existing `goal_create` tool
  and using the same field semantics as `goal_create`
- `assistant_message`: short user-facing summary of what will be created

For Phase B, create proposals are pinned to the existing finance spending-cap convention:

- `goal_type: "spending_cap"`
- `metric_type: "sum_below"`
- `domain: "finance"`
- `target_value`: positive integer cents, not a dollar float/string

### 2. `update`

The message was interpreted as an update to an existing goal and Core can resolve the target goal safely.

The response includes:

- `result_type: "update"`
- `action`: `"goal_update"`
- `goal_id`
- `payload`: structured `goal_update` fields using the same field semantics as
  `goal_update`
- `assistant_message`: short user-facing summary of what will be updated

If an update proposal includes `target_value`, it must be expressed as positive
integer cents so the payload can be passed directly to `goal_update`.

### 3. `clarify`

The message is goal-related, but required information is missing or ambiguous.

The response includes:

- `result_type: "clarify"`
- `clarification_type`
- `question`
- optional `options`
- optional `action`
- optional `resume_payload`

Phase B clarification types should be a pinned small enum:

- `missing_target`
- `ambiguous_goal`
- `ambiguous_subject`
- `missing_goal`
- `vague_intent`

Typical clarification cases:

- missing target value
- missing period
- ambiguous goal match for updates
- ambiguous create subject that could be either a category or a merchant
- no active goal exists to satisfy an update request
- vague goal intent with no actionable metric

For `ambiguous_goal` on an update path, the clarify response must also carry the
pending mutation so the harness does not need to reconstruct it:

- `action: "goal_update"`
- `resume_payload`: the structured `goal_update` fields that should be applied
  once the user picks a specific goal

That allows the harness to ask the question, capture a selected `goal_id` from
`options`, and then call `goal_update(goal_id=selected_goal_id,
**resume_payload)` without re-parsing the original utterance.

### 4. `no_match`

The message does not look like a goal create/update request.

The response includes:

- `result_type: "no_match"`
- `assistant_message`

## Mutation Rule

`goal_capture` must not persist changes by itself.

It returns a proposal only. The harness or caller must still call:

- `goal_create` for `create`
- `goal_update` for `update`

This keeps the mutation boundary explicit and preserves the current validation and error handling in the structured tools.

`assistant_message` should be treated as an untrusted client-facing summary field rather than as an authoritative structured field. In Phase B it should be produced from deterministic templates over normalized action results and should not echo raw user text beyond the normalized goal subject needed to explain the proposal.

For clarify outcomes, `resume_payload` is also an untrusted client-facing helper
field. It is only a structured continuation hint for the harness and does not
replace the validation performed by `goal_create` or `goal_update`.

## Interpretation Policy

Phase B should be deliberately narrow and finance-first.

Supported create intents:

- spending cap goals with explicit dollar targets and a recognizable spending subject
  where the extracted target is converted into integer cents for the returned
  payload

Supported update intents:

- pause a goal
- resume or unpause a goal
- archive a goal
- change a target value for a goal

Supported subject resolution:

- category-based finance goals such as dining out or groceries
- merchant-based finance goals when the merchant name is explicit

For Phase B, supported create proposals are intentionally limited to one goal family:

- `goal_type: "spending_cap"`
- `metric_type: "sum_below"`

Anything that would require `sum_above`, `count_below`, `count_above`, or a non-finance goal type is out of scope for Phase B and should return `no_match` or `clarify` rather than silently inventing a broader interpretation.

Out of scope for Phase B:

- broad abstract goals like "be better with money"
- multi-step conversational decomposition
- cross-domain natural language parsing
- implicit metric inference for vague text without enough structure

## Resolution Rules

### Create Resolution

For create intents, the policy should:

1. identify that the user is creating a goal
2. infer a finance domain default where appropriate
3. map common phrases like "dining out" and "groceries" into finance filters
4. extract dollar target and period when explicitly present
5. ask for clarification when required fields are missing or the resolved
   subject is ambiguous

The returned payload should still respect existing Core defaults where appropriate, such as `domain="finance"`.

If create interpretation succeeds, the payload must satisfy all of these invariants before it can be returned:

- `goal_type == "spending_cap"`
- `metric_type == "sum_below"`
- `domain == "finance"`
- `target_value` is a positive integer number of cents
- `period` is one of the existing Core-supported periods
- at least one finance filter is present
- the payload is structurally suitable for `goal_create`

### Category Resolution Strategy

Phase B should not hardcode finance category names into the conversational policy.

Instead, the policy should resolve conversational category subjects against actual category names available through the finance read boundary.

Recommended behavior:

1. fetch the current goal-eligible category names from the finance layer
2. normalize the user subject and candidate category names
3. prefer exact normalized matches
4. allow a small pinned alias map for common phrases such as:
   - `dining out` -> try to match a category name like `Dining Out`
   - `groceries` -> try to match a category name like `Groceries`
5. apply alias matching by resolving the alias phrase to a normalized candidate string and then requiring exact normalized equality against one real finance category name from the database; Phase B should not use substring or fuzzy containment for category-name resolution
6. if exactly one category matches, use that real category name in `category_names`
7. if zero or multiple categories match, return `clarify` rather than emitting a functionally useless goal

This means Phase B will require a small extension to the finance read interface so Core can retrieve category names without querying finance tables directly.

### Merchant Resolution Strategy

Phase B should apply the same deterministic posture to merchant-scoped goals as
it does to category-scoped goals.

Recommended behavior:

1. fetch distinct nonblank merchant names from expense transactions through the
   finance read layer
2. normalize the user subject and candidate merchant names
3. prefer exact normalized matches
4. allow a small pinned alias map only when the alias resolves to exact
   normalized equality against one real merchant name from the database
5. do not use substring or fuzzy containment for merchant-name resolution in
   Phase B
6. if exactly one merchant matches, use that real merchant name in
   `merchant_names`
7. if zero or multiple merchants match, return `clarify` rather than guessing

If a subject resolves to exactly one category and exactly one merchant, return
`clarify` rather than choosing silently.

Pinned create-time clarification contract for that case:

- `clarification_type: "ambiguous_subject"`
- `action: "goal_create"`
- `resume_payload`: the fully resolved `goal_create` payload except for the
  unresolved subject filter
- `options`: a list of lightweight objects with this shape:
  `{"kind": "category" | "merchant", "label": str, "payload_fragment": {"category_names"?: list[str], "merchant_names"?: list[str]}}`

That lets the harness ask the question, let the user choose one option, merge
the selected `payload_fragment` into `resume_payload`, and then call
`goal_create(**final_payload)` without re-parsing the original utterance.

This means Phase B will require a small extension to the finance read interface
so Core can retrieve merchant names without querying finance tables directly.

### Finance Read Boundary Extensions

To support deterministic subject resolution without direct Core queries against
finance tables, Phase B should extend the finance read boundary with:

- `list_goal_category_names() -> list[str]`
- `list_spending_merchant_names() -> list[str]`

These methods should return stable deterministic lists with these semantics:

- category names are distinct configured category names that are eligible for
  spending-cap goals, even if they have no expense history yet
- the category list should include seeded or manually created spend categories
  with zero matching transactions
- the category list should exclude pinned non-spend categories that are not
  meaningful for `sum_below` spending-cap goals; for Phase B this excludes
  `Income`
- merchant names are distinct nonblank merchant names that have appeared on at
  least one expense transaction (`amount_cents < 0`)
- both lists are sorted ascending for deterministic matching and tests
- neither method should expose blank names

Core should use these methods rather than querying finance tables directly.

### Title Generation

Phase B should generate deterministic titles for capture-created finance goals.

Pinned template:

- category-scoped goal: `"{Resolved Subject} Spending Cap"`
- merchant-scoped goal: `"{Resolved Subject} Spending Cap"`

Examples:

- `Dining Out Spending Cap`
- `Groceries Spending Cap`
- `Cafe Spending Cap`

The title should be built from the resolved subject string, not from the raw user utterance. This keeps titles predictable for later updates and avoids echoing unnecessary user text.

Merchant-scoped examples in this phase assume the user subject resolves to one
real merchant name after normalization. Phase B does not promise canonical
brand collapsing across merchant variants that are only loosely related in raw
imported transaction data.

### Relative Period Resolution

When the user uses phrases like `this month`, `this week`, or `today`, `goal_capture` should use `review_date` to resolve both the period and the starting boundary.

For Phase B:

- `this month` -> `period="monthly"` and `starts_on` set to the first day of the anchored month
- `this week` -> `period="weekly"` and `starts_on` set to the first day of the anchored week using the same week boundary Core already assumes for weekly goal progress
- `today` -> `period="daily"` and `starts_on` set to the anchored date

Every returned create proposal must include an explicit `starts_on` value
resolved at capture time.

If no relative period phrase is present, `starts_on` should be set to the
anchored current date at capture time, where the anchor is `review_date` when
provided and otherwise the machine-local current date. `goal_capture` should
not rely on `goal_create` to fill this default later, because capture and
execution are separate steps.

### Update Resolution

For update intents, the policy should:

1. identify the intended operation such as pause, resume, archive, or retarget
2. search existing active or paused supported-family goals in Core
3. resolve exactly one target goal when possible
4. return `clarify` if the target goal is missing or ambiguous

This means update interpretation may depend on current Core goal state.

Conversational updates in Phase B are intentionally limited to the same goal
family as conversational creates. Eligible update targets must satisfy all of:

- `goal_type == "spending_cap"`
- `metric_type == "sum_below"`
- `domain == "finance"`
- `status in {"active", "paused"}`

Goals outside that family are out of scope for `goal_capture` updates in this
phase and must not be selected as update targets.

Pinned update mapping for Phase B:

- pause -> `payload = {"status": "paused"}`
- resume or unpause -> `payload = {"status": "active"}`
- archive -> `payload = {"status": "archived"}`
- change target value -> `payload = {"target_value": <positive integer cents>}`

If no active or paused goal can be found for an update-like utterance, return:

- `result_type: "clarify"`
- `clarification_type: "missing_goal"`

If the user utterance appears to target a goal outside the supported
conversational family, return `no_match` rather than emitting a payload that
would apply misleading cents-based semantics to a different metric family.

To distinguish `missing_goal` from unsupported-family `no_match`
deterministically, Phase B should first do a broader non-selecting match pass
across candidate goals before applying the supported-family filter used for
final update targeting.

Phase B update resolution should search only supported-family goals whose
status is in `{"active", "paused"}`. Archived goals are not valid update
targets in this phase.

## Goal Matching Rules

Phase B should use deterministic matching rather than an LLM-only black box.

Recommended matching order:

1. exact title match
2. normalized title containment
3. filter-aware finance subject match for known category or merchant names

If more than one active or paused supported-family goal plausibly matches,
return `clarify` with short options rather than choosing silently.

For `ambiguous_goal`, `options` should be a list of lightweight objects rather than plain titles:

- `{"goal_id": int, "title": str, "period": str, "target_value": int, "status": str, "filter_summary": str}`

`target_value` should remain in integer cents so the contract stays consistent
with the structured goal tools.

That gives the harness enough information to render the choice, distinguish
otherwise-identical generated titles, and route a follow-up selection back to
the correct goal.

## Error Model

`goal_capture` should use the same MCP contract wrapper behavior as the other Core tools.

Expected failure handling:

- invalid `review_date` -> `INVALID_INPUT`
- empty or too-long `message` -> `INVALID_INPUT`
- transient infrastructure failure during goal lookup -> framework error wrapped as `INTERNAL_ERROR`

Interpretation failures should not be raised as tool errors when the request is still user-actionable. Those should return structured outcomes instead:

- missing information -> `clarify`
- ambiguous goal target -> `clarify`
- unsupported or non-goal utterance -> `no_match`

## LLM Use

Phase B should not use the LLM path.

The interpretation surface is narrow enough that deterministic heuristics are preferable:

- easier to test
- lower-risk at the MCP boundary
- no chance of structurally valid but policy-invalid goal proposals leaking through

An LLM-assisted interpretation path can be considered in a later slice once the deterministic contract is stable.

## Tool Contract Stability

`goal_capture` should produce payloads that align exactly with existing Core tool contracts:

- `goal_create`
- `goal_update`

The new tool should not invent a second goal schema.

That means:

- if the proposed create payload would be invalid, return `clarify` instead of a broken create proposal
- if an update cannot be tied to one existing goal, return `clarify`

This also means `goal_capture` must not emit a structurally valid payload that falls outside the Phase B policy invariants above.

## End-To-End Verification In Repo

The repo-level end-to-end proof for Slice 2.1 should be:

1. call `goal_capture` with a natural-language create request
2. execute the returned `goal_create` payload
3. seed at least one matching finance transaction so goal progress is exercised against real data
4. call `goal_get` for the created goal at the relevant `review_date`
5. verify the returned progress reflects the seeded transaction data
6. call `goal_capture` with a natural-language update request against that goal
7. execute the returned `goal_update` payload
8. call `goal_get` again and verify the update changed the expected goal fields
9. call protected `daily_review`
10. verify the protected review contract still hides raw goal text while the internal review pipeline remains goal-aware

This gives the repo a real conversational-goal-through-Core flow without requiring Discord code to live here.

## Hermes Integration Plan

The external Hermes or Discord integration should use this sequence:

1. user sends goal language to Hermes
2. Hermes calls `goal_capture(message=...)`
3. if result is `clarify`, Hermes asks the returned question
4. if result is `create`, Hermes calls `goal_create(**payload)`
5. if result is `update`, Hermes calls `goal_update(goal_id=..., **payload)`
6. Hermes may then call protected `daily_review` or `goal_get` to summarize next state

For `ambiguous_goal` update clarifications, Hermes should not re-parse the
original message. It should:

1. render the returned `options`
2. capture the selected `goal_id`
3. call `goal_update(goal_id=selected_goal_id, **resume_payload)`

Hermes should not duplicate:

- phrase-to-payload mapping
- goal matching policy
- ambiguity handling rules

## Expected Implementation Surfaces

- `minx_mcp/core/server.py`
  Register the new `goal_capture` tool.
- `minx_mcp/core/models.py`
  Add typed result models for goal capture responses if helpful and extend
  `FinanceReadInterface` with the new subject-list methods.
- `minx_mcp/core/`
  Add a focused conversational goal policy/interpreter module.
- `minx_mcp/finance/read_api.py`
  Implement the new deterministic finance subject-list methods.
- `tests/test_core_server.py`
  Add goal capture contract tests.
- `tests/test_core_mcp_stdio.py`
  Add stdio flow coverage for `goal_capture`.
- `tests/test_finance_read_api.py`
  Add coverage for the new expense-scoped category and merchant list methods.
- `tests/`
  Add end-to-end tests that chain `goal_capture` -> goal mutation -> protected review.

## Success Criteria

Phase B is successful when:

- `minx-core` exposes a stable `goal_capture` tool
- narrow finance-first conversational goal creation works without client-specific business logic
- conversational update intents can safely resolve existing goals or return clarifications
- the end-to-end repo flow from conversational input to protected review is covered by tests
- the actual Hermes wiring remains outside this repo and can stay thin

## Follow-On Work

This repo now satisfies the repo-scoped Slice 2.1 conversational-goals outcome:

- Phase A: trust hardening at the review boundary
- Phase B: conversational goal interpretation over Core tools

After that, the next major repo slice should be Slice 3 unless product priorities change again. Harness-specific instance setup remains intentionally deferred to later harness-adaptation work outside this repo.
