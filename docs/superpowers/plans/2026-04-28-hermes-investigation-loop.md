# Hermes Investigation Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Slice 9d Hermes-side `minx_investigate` loop that uses Core investigation APIs for durable audit rows while Hermes owns tool choice, budgets, confirmations, and final answer prose.

**Architecture:** Core is already implemented: it stores investigation lifecycle rows, digest-only trajectory steps, render hints, citations, and terminal status. This plan works in `minx-hermes` first because that repo owns Minx-specific Hermes skills/scripts; if the live Hermes runtime hook must be patched in `hermes-agent`, use the runner and catalog from this plan as the contract and adapter reference. The loop must never persist raw domain tool output in Core; it appends SHA-256 digests, small counts/labels, and typed citation refs only.

**Tech Stack:** Bash, Python 3.12, MCP streamable HTTP client (`mcp.client.streamable_http`), `httpx`, SQLite smoke inspection, existing Minx MCP HTTP servers, `minx-hermes` skill files and scripts.

---

## File Map

- Create `minx-hermes/docs/minx-investigation-tool-catalog.md`: concrete read-first tool catalog for Finance, Meals, Training, Core memory/goals/insights, and blocked mutation tools.
- Modify `minx-hermes/skills/minx/investigate/SKILL.md`: replace generic allowlist language with concrete current tool names, budget rules, digest/citation rules, and confirmation behavior.
- Create `minx-hermes/scripts/minx-investigate-once.py`: deterministic smoke runner that exercises the same MCP start -> tool -> digest append -> complete lifecycle that Hermes must use.
- Modify `minx-hermes/scripts/smoke-investigations.sh`: add validation that a new terminal row has digest-shaped trajectory entries, citations when available, and no obvious raw-output fields.
- Modify `minx-hermes/README.md`: document how to run the investigation smoke flow.
- Modify `HANDOFF.md`: link this plan from the Slice 9d next-step section.

---

### Task 1: Tool Catalog And Skill Tightening

**Files:**
- Create: `minx-hermes/docs/minx-investigation-tool-catalog.md`
- Modify: `minx-hermes/skills/minx/investigate/SKILL.md`
- Verify: `minx-hermes/README.md` only if the repo currently lists Minx skills/scripts

- [ ] **Step 1: Create the safe tool catalog**

Create `minx-hermes/docs/minx-investigation-tool-catalog.md` with these sections and concrete tool names:

```markdown
# Minx Investigation Tool Catalog

This catalog defines the default tool policy for `minx_investigate`. Investigations are read-first. Hermes may call read tools freely within budget, but mutation tools require explicit user confirmation and an `investigation.needs_confirmation` step before any state change.

## Core Read Tools

- `get_daily_snapshot(review_date?, force=false)`: read daily finance, nutrition, training, goal, and insight summary state.
- `get_insight_history(limit?, domain?, start_date?, end_date?)`: read historical insights.
- `get_goal_trajectory(goal_id)`: read trajectory for a known goal.
- `goal_list(...)`, `goal_get(goal_id)`: read goal state.
- `memory_list(status?, memory_type?, scope?, limit?)`: browse memory records.
- `memory_get(memory_id)`: fetch one memory.
- `memory_search(query, scope?, memory_type?, status?, limit?)`: deterministic FTS search.
- `memory_hybrid_search(query, scope?, memory_type?, status?, limit?)`: FTS plus embedding rerank when configured.
- `memory_edge_list(memory_id, direction?, predicate?, limit?)`: traverse memory graph relationships.
- `investigation_history(kind?, harness?, status?, limit?)`: find prior investigations.
- `investigation_get(investigation_id)`: inspect one prior investigation.

## Finance Read Tools

- `safe_finance_summary()`: safe aggregate overview.
- `safe_finance_accounts()`: account overview without sensitive transaction rows.
- `finance_query(message?, filters?, limit?)`: natural-language or structured finance query with Core render/slot contract.
- `finance_anomalies()`: anomaly summary.
- `finance_monitoring(period_start, period_end)`: monitoring summary for a bounded period.
- `finance_job_status(job_id)`: read import/report job status.

## Meals Read Tools

- `pantry_list()`: current pantry rows.
- `recommend_recipes(include_needs_shopping?, max_results?)`: recipe recommendations.
- `nutrition_profile_get()`: current nutrition profile.
- `recipe_template()`: recipe note scaffold.

## Training Read Tools

- `training_exercise_list()`: current exercise catalog.
- `training_program_get(program_id)`: one program.
- `training_session_list(start_date?, end_date?, limit?)`: sessions in a bounded date range.
- `training_progress_summary(as_of?, window_days?)`: progress summary.

## Mutation Tools Requiring Confirmation

- Core: `memory_create`, `memory_capture`, `memory_confirm`, `memory_reject`, `memory_expire`, `vault_replace_section`, `vault_replace_frontmatter`, `persist_note`, `goal_create`, `goal_update`, `goal_archive`.
- Finance: `finance_import`, `finance_import_preview` when it stages files, `finance_categorize`, `finance_add_category_rule`, report generation tools if they persist jobs.
- Meals: `meal_log`, `pantry_add`, `pantry_update`, `pantry_remove`, `recipe_index`, `recipe_scan`, `recipes_reconcile`, `nutrition_profile_set`.
- Training: `training_exercise_upsert`, `training_program_upsert`, `training_program_activate`, `training_session_log`.

## Default Budgets

- `max_tool_calls`: 12 total MCP calls after `start_investigation`.
- `wall_clock_s`: 120 seconds.
- `max_large_output_bytes`: 64 KiB inspected by Hermes; digest the full result but summarize only counts/labels in Core.
- `max_steps`: 12 appended trajectory steps.

When a budget is exhausted, complete the investigation with `status="budget_exhausted"` and a partial answer.
```

- [ ] **Step 2: Tighten `investigate/SKILL.md`**

Replace the current generic "Existing read-only meals/training tools" language with an explicit link to `docs/minx-investigation-tool-catalog.md` and a compact allowlist summary:

```markdown
## Tool Allowlist

Use the concrete catalog in `docs/minx-investigation-tool-catalog.md`.

Default read tools:

- Core: `get_daily_snapshot`, `get_insight_history`, `get_goal_trajectory`, `goal_list`, `goal_get`, `memory_list`, `memory_get`, `memory_search`, `memory_hybrid_search`, `memory_edge_list`, `investigation_history`, `investigation_get`
- Finance: `safe_finance_summary`, `safe_finance_accounts`, `finance_query`, `finance_anomalies`, `finance_monitoring`, `finance_job_status`
- Meals: `pantry_list`, `recommend_recipes`, `nutrition_profile_get`, `recipe_template`
- Training: `training_exercise_list`, `training_program_get`, `training_session_list`, `training_progress_summary`

Do not call mutation tools during an investigation unless the user explicitly confirms that action. If a mutation looks useful, append `event_template="investigation.needs_confirmation"`, ask the user, and stop the loop until they decide.
```

- [ ] **Step 3: Verify catalog has no stub tool names**

Run from `minx-hermes`:

```bash
rg "meals_list|training_list|FILL_ME|REPLACE_ME" docs/minx-investigation-tool-catalog.md skills/minx/investigate/SKILL.md
```

Expected: no matches.

- [ ] **Step 4: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 2: Deterministic Investigation Runner

**Files:**
- Create: `minx-hermes/scripts/minx-investigate-once.py`
- Modify: `minx-hermes/README.md`

- [ ] **Step 1: Add the runner skeleton**

Create `scripts/minx-investigate-once.py` with an executable Python script that accepts:

```text
--question "..."
--core-url http://127.0.0.1:8001/mcp
--finance-url http://127.0.0.1:8000/mcp
--meals-url http://127.0.0.1:8002/mcp
--training-url http://127.0.0.1:8003/mcp
--mode finance-summary|daily-snapshot
```

Use this structure:

```python
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def call_tool(url: str, name: str, arguments: dict[str, object]) -> dict[str, Any]:
    async with (
        httpx.AsyncClient(timeout=30.0) as http_client,
        streamable_http_client(url, http_client=http_client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(name, arguments)
        if result.isError:
            raise RuntimeError(f"{name} returned MCP error")
        structured = result.structuredContent
        if not isinstance(structured, dict):
            raise RuntimeError(f"{name} did not return structured content")
        return structured
```

- [ ] **Step 2: Implement `start_investigation` and step append**

Add helpers that call Core and append one digest step per domain tool call:

```python
async def start_investigation(core_url: str, question: str) -> int:
    result = await call_tool(
        core_url,
        "start_investigation",
        {
            "kind": "investigate",
            "question": question,
            "context_json": {"runner": "minx-investigate-once", "mode": "smoke"},
            "harness": "hermes",
        },
    )
    return int(result["data"]["investigation"]["id"])


async def append_step(
    core_url: str,
    investigation_id: int,
    *,
    step: int,
    tool: str,
    args: dict[str, object],
    result: dict[str, Any],
    latency_ms: int,
    summary: str,
    row_count: int | None = None,
) -> None:
    event_slots: dict[str, object] = {"summary": summary}
    if row_count is not None:
        event_slots["row_count"] = row_count
    await call_tool(
        core_url,
        "append_investigation_step",
        {
            "investigation_id": investigation_id,
            "step_json": {
                "step": step,
                "event_template": "investigation.step_logged",
                "event_slots": event_slots,
                "tool": tool,
                "args_digest": canonical_digest(args),
                "result_digest": canonical_digest(result),
                "latency_ms": latency_ms,
            },
        },
    )
```

- [ ] **Step 3: Implement two smoke modes**

Implement:

- `finance-summary`: call `safe_finance_summary`, append a digest step, and complete with an answer that cites the tool result digest.
- `daily-snapshot`: call `get_daily_snapshot` with today's local date unless `--review-date YYYY-MM-DD` is supplied, append a digest step, and complete with an answer that cites the tool result digest.

The terminal call must use Core:

```python
async def complete_investigation(
    core_url: str,
    investigation_id: int,
    *,
    answer_md: str,
    citation_refs: list[dict[str, object]],
    tool_call_count: int,
) -> None:
    await call_tool(
        core_url,
        "complete_investigation",
        {
            "investigation_id": investigation_id,
            "status": "succeeded",
            "answer_md": answer_md,
            "citation_refs": citation_refs,
            "tool_call_count": tool_call_count,
        },
    )
```

- [ ] **Step 4: Handle failures after start**

Wrap the main flow so any exception after `start_investigation` calls:

```python
await call_tool(
    core_url,
    "complete_investigation",
    {
        "investigation_id": investigation_id,
        "status": "failed",
        "answer_md": "",
        "error_message": str(exc)[:500],
        "tool_call_count": tool_call_count,
    },
)
```

Then re-raise so the smoke command fails visibly.

- [ ] **Step 5: README usage**

Add a short README section:

````markdown
### Smoke-test a deterministic investigation

Start Minx MCP servers from the `minx` repo:

```bash
scripts/start_hermes_stack.sh
```

In `minx-hermes`, run:

```bash
./scripts/smoke-investigations.sh -- \
  python3 scripts/minx-investigate-once.py \
    --question "Summarize my current finance state" \
    --mode finance-summary
```
````

- [ ] **Step 6: Run deterministic smoke**

Run from `minx-hermes` while Minx servers are running:

```bash
./scripts/smoke-investigations.sh -- \
  python3 scripts/minx-investigate-once.py \
    --question "Summarize my current finance state" \
    --mode finance-summary
```

Expected: the smoke helper prints a new terminal `investigations` row with `status=succeeded` and `tool_call_count >= 1`.

- [ ] **Step 7: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 3: Stronger Smoke Validation

**Files:**
- Modify: `minx-hermes/scripts/smoke-investigations.sh`

- [ ] **Step 1: Add trajectory validation**

Extend `wait_for_terminal_investigation()` to select `trajectory_json` and `citation_refs_json` for the terminal row. Add Python validation:

```python
import json
import re

DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_KEYS = {
    "raw",
    "raw_output",
    "output",
    "result",
    "response",
    "messages",
    "transcript",
    "rows",
    "transactions",
}

trajectory = json.loads(run["trajectory_json"] or "[]")
if not isinstance(trajectory, list) or not trajectory:
    raise SystemExit("ERROR: terminal investigation has no trajectory steps")

for index, step in enumerate(trajectory, start=1):
    if not isinstance(step, dict):
        raise SystemExit(f"ERROR: trajectory step {index} is not an object")
    for key in ("args_digest", "result_digest"):
        if not DIGEST_RE.match(str(step.get(key, ""))):
            raise SystemExit(f"ERROR: trajectory step {index} has invalid {key}")
    event_slots = step.get("event_slots", {})
    if isinstance(event_slots, dict) and FORBIDDEN_KEYS.intersection(event_slots):
        raise SystemExit(f"ERROR: trajectory step {index} stores raw-output-like event_slots keys")

citations = json.loads(run["citation_refs_json"] or "[]")
if citations and not isinstance(citations, list):
    raise SystemExit("ERROR: citation_refs_json is not a list")
```

- [ ] **Step 2: Verify schema and validation paths**

Run:

```bash
./scripts/smoke-investigations.sh --check-schema
./scripts/smoke-investigations.sh --history
```

Expected: schema check prints `investigations schema OK`; history prints recent rows or just the table header if none exist.

- [ ] **Step 3: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 4: Runtime Integration Handoff

**Files:**
- Modify: `minx-hermes/skills/minx/investigate/SKILL.md`
- Create: `minx-hermes/docs/hermes-investigation-runtime-contract.md`

- [ ] **Step 1: Create runtime contract doc**

Create `docs/hermes-investigation-runtime-contract.md`:

```markdown
# Hermes Investigation Runtime Contract

The live Hermes runtime must expose `minx_investigate(question, context?)` with the same lifecycle used by `scripts/minx-investigate-once.py`.

Required behavior:

1. Call `minx_core.start_investigation(kind="investigate", harness="hermes", question=..., context_json=...)` before any domain tool call.
2. Choose read tools from `docs/minx-investigation-tool-catalog.md`.
3. For each tool call, hash canonical JSON arguments and full structured result with lowercase SHA-256 hex.
4. Call `minx_core.append_investigation_step` with only digests, tool name, latency, and small summary slots.
5. Stop at 12 domain tool calls or 120 seconds unless the user explicitly gives a smaller budget.
6. Complete terminally with `status="succeeded"`, `status="failed"`, `status="cancelled"`, or `status="budget_exhausted"`.
7. Include typed `citation_refs` for durable memories, prior investigations, vault paths, and ephemeral tool result digests.
8. Never persist raw domain tool outputs, transcripts, rows, or messages in Core investigation fields.

The deterministic runner is the executable reference for lifecycle and digest behavior. The real LLM loop may choose tools dynamically, but it must preserve this storage contract.
```

- [ ] **Step 2: Link the runtime contract from the skill**

Add to `skills/minx/investigate/SKILL.md`:

```markdown
## Runtime Contract

The live Hermes implementation must follow `docs/hermes-investigation-runtime-contract.md`. The deterministic runner `scripts/minx-investigate-once.py` is the reference smoke implementation for Core lifecycle calls, digest generation, terminal completion, and citation refs.
```

- [ ] **Step 3: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 5: End-To-End Verification

**Files:**
- No source edits unless verification exposes a defect.

- [ ] **Step 1: Verify `minx-hermes` shell scripts**

Run from `minx-hermes`:

```bash
bash -n scripts/smoke-investigations.sh
```

Expected: no output and exit code `0`.

- [ ] **Step 2: Verify Python runner imports**

Run from `minx-hermes`:

```bash
python3 -m py_compile scripts/minx-investigate-once.py
```

Expected: no output and exit code `0`.

- [ ] **Step 3: Verify docs have no stub markers**

Run from `minx-hermes`:

```bash
rg "FILL_ME|REPLACE_ME|meals_list|training_list" README.md docs skills scripts
```

Expected: no matches.

- [ ] **Step 4: Run Core schema smoke**

Run from `minx-hermes`:

```bash
./scripts/smoke-investigations.sh --check-schema
```

Expected: `investigations schema OK`.

- [ ] **Step 5: Run one deterministic investigation**

Start Minx MCP servers from the `minx` repo:

```bash
scripts/start_hermes_stack.sh
```

Then run from `minx-hermes`:

```bash
./scripts/smoke-investigations.sh -- \
  python3 scripts/minx-investigate-once.py \
    --question "Summarize my current finance state" \
    --mode finance-summary
```

Expected: terminal row with `status=succeeded`, `tool_call_count >= 1`, digest-shaped `trajectory_json`, and no raw-output-like keys in `event_slots`.

- [ ] **Step 6: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

## Self-Review

- Spec coverage: Core contract requirements are already implemented by Slice 9. This plan covers the remaining Hermes-side loop: concrete tool catalog, budgets, digest-only step append, citations, terminal completion, confirmation stop point, and smoke validation.
- Boundary check: No Core API changes are planned here. If the Hermes runtime repository must be patched, use `scripts/minx-investigate-once.py` and `docs/hermes-investigation-runtime-contract.md` as the adapter contract.
- Stub-name scan: The plan names the current Finance, Meals, Training, Core memory, goal, and investigation tools.
- Commit policy: Every task ends with a review checkpoint. Do not commit unless the user explicitly asks for a commit in the current session.
