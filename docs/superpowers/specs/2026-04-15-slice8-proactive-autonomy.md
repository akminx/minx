# Slice 8: Proactive Autonomy

**Date:** 2026-04-15 (revised 2026-04-19)
**Status:** Designed — ready to implement (all Slice 6 dependencies landed through 6f)
**Depends on:** Slice 6a–6f (memory CRUD + vault scanner + reconciler all merged on `main`)
**Design plan:** `docs/superpowers/plans/2026-04-15-slice6-and-8-design.plan.md`

**2026-04-19 revision notes:**
- Slice 6a–6f are complete on `main`; all condition-data tools referenced below exist. Verified against `minx_mcp/core/server.py`.
- Tool-name corrections (`finance_generate_weekly_report`, `vault_replace_section`).
- Template resource exists today at `wiki-templates://{name}` — not optional for 8d.
- Two-phase run logging (`start_playbook_run` + `complete_playbook_run`) added to close the crash-recovery gap; single-tool `log_playbook_run` kept for fire-and-forget cases.
- Pinned wiki-generated page frontmatter `type: minx-wiki` so they stay out of the `minx-memory` scanner/reconciler surface.
- Integration map added (§12) naming the files each phase lands in.

## 1) Goal

Give Minx the ability to act without being asked — daily reviews, weekly reports, memory curation, wiki maintenance, and goal nudges — while keeping every action bounded, auditable, and killable.

## 2) Key Architecture Decision: Core vs Harness Split

**Scheduling and orchestration belong to the harness, not Core.**

This follows the governing principle established in Slice 2.5: data and deterministic logic live in MCP; conversational policy, rendering, and scheduling live in the harness.

### What Core Provides

Core is a toolbox. It stores audit data, publishes a playbook manifest, and exposes condition-checking tools. It does not schedule, orchestrate, or make notification decisions.

| Responsibility | Implementation |
|---------------|---------------|
| Audit trail storage | `playbook_runs` table |
| Logging tool | `log_playbook_run` MCP tool |
| History query | `playbook_history` MCP tool |
| Playbook manifest | `playbook://registry` MCP resource |
| Condition data | Existing tools (`get_daily_snapshot`, `get_pending_memory_candidates`, etc.) |

### What the Harness (Hermes) Provides

The harness is the operator. It decides when to run playbooks, how to interact with the user, and what to write to the vault.

| Responsibility | Implementation |
|---------------|---------------|
| Scheduling | Hermes cron infrastructure (already exists) |
| Playbook runner scripts | Scripts that call Core MCP tools in sequence |
| Confirmation flows | Conversation layer (Hermes skill) |
| Wiki maintenance | LLM Wiki pattern (LLM generates pages, writes via `persist_note`) |
| Notification decisions | Harness policy (DM, vault note, or silence) |

### Why Not APScheduler in Core?

The original plan embedded APScheduler in Core. This was reconsidered because:
1. It reverses the Slice 2.5 decision to keep orchestration out of Core
2. Hermes already has cron infrastructure — two schedulers creates conflicts
3. It makes Core less portable to other harnesses
4. Confirmation gates are a conversation concern, not a data concern

The playbook *definitions* (MCP resource) and *audit trail* (Core DB) travel with you to any harness. Only the scheduling scripts are harness-specific.

## 3) Schema

### `playbook_runs` table

Lives in migration `019_playbook_runs.sql` (next free number after `018_vault_index.sql`).

```sql
CREATE TABLE playbook_runs (
    id INTEGER PRIMARY KEY,
    playbook_id TEXT NOT NULL,
    harness TEXT NOT NULL,              -- 'hermes', 'cli', etc.
    triggered_at TEXT NOT NULL,         -- ISO8601 UTC
    trigger_type TEXT NOT NULL          -- 'cron' | 'event' | 'manual'
        CHECK (trigger_type IN ('cron', 'event', 'manual')),
    trigger_ref TEXT,                   -- cron expression, event_id, or 'manual'
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'skipped', 'succeeded', 'failed')),
    conditions_met INTEGER,             -- NULL while running; 0/1 once evaluated
    action_taken INTEGER,               -- NULL while running; 0/1 once settled
    result_json TEXT,                   -- shape defined per-playbook in registry
    error_message TEXT,
    completed_at TEXT                   -- NULL until terminal status reached
);

CREATE INDEX idx_playbook_runs_playbook_triggered
    ON playbook_runs(playbook_id, triggered_at DESC);

-- Prevents double-fire of the same cron tick. Two concurrent harness processes
-- racing on a 09:00 daily_review tick both try to INSERT with the same
-- (playbook_id, trigger_type, trigger_ref); the second loses.
-- SQLite UNIQUE allows multiple NULLs; COALESCE makes NULL refs dedupe correctly.
CREATE UNIQUE INDEX idx_playbook_runs_in_flight
    ON playbook_runs(playbook_id, trigger_type, COALESCE(trigger_ref, ''))
    WHERE status = 'running';
```

**Why two-phase (running → terminal):** a single-insert audit log loses information when the harness crashes between "I decided to run this" and "I finished." The `running` row is inserted *before* condition checks, so a crashed run leaves visible evidence and the next invocation can reconcile it (`playbook_reconcile_crashed` flips orphaned `running` rows older than N minutes to `failed` with `error_message='harness crash suspected'`).

## 4) MCP Tools (Core server)

### `start_playbook_run`

Called by the harness *before* condition checks. Inserts a `running` row and returns its id. The unique index on `(playbook_id, trigger_type, trigger_ref) WHERE status='running'` will reject a concurrent duplicate; the harness MUST treat that rejection as "another worker has the tick" and exit.

```
start_playbook_run(
    playbook_id: str,
    harness: str,
    trigger_type: str,          # 'cron' | 'event' | 'manual'
    trigger_ref: str | None,
) -> {"run_id": int}  # raises CONFLICT on duplicate in-flight row
```

### `complete_playbook_run`

Called when the run reaches a terminal state. Flips `status`, stamps `completed_at`, writes `conditions_met` / `action_taken` / `result_json` / `error_message`.

```
complete_playbook_run(
    run_id: int,
    status: str,                # 'skipped' | 'succeeded' | 'failed'
    conditions_met: bool,
    action_taken: bool,
    result_json: str | None,
    error_message: str | None,
) -> {"run_id": int}
```

### `log_playbook_run` (convenience)

Fire-and-forget wrapper for synchronous playbooks that can't crash mid-run (e.g. pure-SQL playbooks). Wraps start + complete in one tool call. The two-phase API remains the recommended path for anything that awaits an LLM or writes to the vault.

```
log_playbook_run(
    playbook_id: str,
    harness: str,
    trigger_type: str,
    trigger_ref: str | None,
    status: str,                # 'skipped' | 'succeeded' | 'failed'
    conditions_met: bool,
    action_taken: bool,
    result_json: str | None,
    error_message: str | None,
) -> {"run_id": int}
```

### `playbook_history`

Query the audit trail for monitoring and debugging.

```
playbook_history(
    playbook_id: str | None = None,
    harness: str | None = None,
    status: str | None = None,      # filter by 'running'/'skipped'/'succeeded'/'failed'
    since: str | None = None,       # ISO8601; overrides `days` when set
    days: int = 30,
    limit: int = 200,               # hard-capped at 1000 server-side
) -> {"runs": [...], "truncated": bool}
```

### `playbook_reconcile_crashed`

Operator tool for sweeping orphan `running` rows. Idempotent.

```
playbook_reconcile_crashed(
    stale_after_minutes: int = 15,
) -> {"reconciled": int, "run_ids": [int, ...]}
```

## 5) MCP Resource: Playbook Registry

Core publishes a `playbook://registry` MCP resource. This is a read-only manifest that any harness can discover. It describes what playbooks exist and how they should be run, without enforcing scheduling.

```python
@dataclass(frozen=True)
class PlaybookDefinition:
    id: str                          # 'daily_review', 'weekly_report', etc.
    name: str
    description: str
    recommended_schedule: str        # '0 21 * * *' (informational, harness decides)
    required_tools: list[str]        # ['get_daily_snapshot', 'persist_note']
    conditions_description: str      # human-readable precondition description
    requires_confirmation: bool      # hint to harness about user approval
```

Initial registry entries use namespaced tool references (`<server>.<tool>`) so validation can cover multi-server playbooks without pretending everything lives on Core.

| Playbook ID | Name | Schedule Hint | Required Tools | Confirmation? |
|------------|------|---------------|----------------|---------------|
| `daily_review` | Daily Review | `0 21 * * *` | `core.get_daily_snapshot`, `core.get_insight_history`, `core.persist_note`, `core.start_playbook_run`, `core.complete_playbook_run` | No |
| `weekly_report` | Weekly Finance Report | `0 10 * * 1` | `finance.finance_generate_weekly_report`, `core.log_playbook_run` | No |
| `wiki_update` | Wiki Maintenance | After daily review | `core.memory_list`, `core.get_daily_snapshot`, `core.persist_note`, `core.vault_replace_section`, `core.start_playbook_run`, `core.complete_playbook_run` | No |
| `memory_review` | Memory Candidate Review | Daily | `core.get_pending_memory_candidates`, `core.memory_confirm`, `core.memory_reject`, `core.start_playbook_run`, `core.complete_playbook_run` | Yes |
| `goal_nudge` | Goal Check-In Nudge | Daily | `core.goal_list`, `core.get_goal_trajectory`, `core.persist_note`, `core.start_playbook_run`, `core.complete_playbook_run` | Yes |

**Template discovery:** the harness fetches wiki scaffolds at runtime via the already-shipped `wiki-templates://{name}` MCP resource (`minx_mcp/core/server.py:257-266`). The registry's `required_tools` list MAY include `wiki-templates://{name}` URIs to make the dependency explicit for operators.

## 6) Condition-Checking Tools (fill gaps)

Most condition data is already available through existing tools. New tools needed:

| Tool | Status (2026-04-19) | Purpose |
|------|--------|---------|
| `get_daily_snapshot` | Live | Returns snapshot data including attention items |
| `get_insight_history` | Live | Historical detector signals |
| `get_pending_memory_candidates` | Live (Slice 6a) | Candidates awaiting user review |
| `memory_list` | Live (Slice 6a) | Active memories for wiki page generation |
| `goal_list`, `get_goal_trajectory` | Live (Slice 2) | Inputs for `goal_nudge` |
| `finance_generate_weekly_report` | Live (Slice 1+) | Deterministic weekly finance render |

No additional condition tools are needed. Slice 8 only adds `playbook_runs` audit tools.

## 7) Harness Playbook Pattern

Each playbook follows this script pattern on the Hermes side:

```
1. Read playbook definition from registry
2. run_id = start_playbook_run(playbook_id, harness, trigger_type, trigger_ref)
   -- If this raises CONFLICT, another worker has the tick; exit silently.
3. Check conditions (call Core MCP tools)
4. If conditions not met:
   a. complete_playbook_run(run_id, status='skipped', conditions_met=False, action_taken=False)
   b. Exit
5. If requires_confirmation:
   a. Surface to user in conversation
   b. Wait for approval/rejection
   c. If rejected:
      complete_playbook_run(run_id, status='skipped', conditions_met=True, action_taken=False)
      Exit
6. Execute action (call Core MCP tools)
7. On success: complete_playbook_run(run_id, status='succeeded', conditions_met=True, action_taken=True, result_json=...)
8. On error: complete_playbook_run(run_id, status='failed', conditions_met=True, action_taken=<bool>, error_message=str(exc))
```

**Crash recovery:** if the harness dies between steps 2 and 7, the `running` row is left dangling. A separate daily cron invokes `playbook_reconcile_crashed`, which flips stale `running` rows to `failed`. Single-tool `log_playbook_run` is acceptable ONLY when steps 3–6 are a single synchronous DB call with no LLM or vault I/O.

### Wiki Maintenance Playbook (LLM Wiki Pattern)

This is the most complex playbook and the primary integration of Karpathy's LLM Wiki concept.

Scheduled after the daily review completes. Steps:

1. `start_playbook_run` (§4).
2. Fetch today's snapshot via `get_daily_snapshot`.
3. Fetch all active memories via `memory_list(status='active')`.
4. For each memory with significant changes since last wiki update:
   a. Fetch the correct scaffold via `wiki-templates://{entity|pattern|goal}` MCP resource.
   b. Generate/update the page body with the harness's LLM (Core does not call the LLM here — the harness owns the LLM binding).
   c. Write via `persist_note` (new page) or `vault_replace_section` (existing page — named tool in `minx_mcp/core/server.py:219`).
   d. Include `[[wikilinks]]` to related entities and patterns.
5. Generate/update the daily review summary page at `Minx/Reviews/YYYY-MM-DD.md` using the `wiki-templates://review` scaffold.
6. `complete_playbook_run`.

The LLM prompt for page generation receives:
- The memory's structured data (from SQLite)
- Recent related insights
- Existing page content (if updating)
- Cross-reference candidates (other memories in the same domain)
- **The canonical page template for that page type** — shipped at `minx_mcp/core/templates/wiki/{entity,pattern,review,goal}.md` and served via `wiki-templates://{name}` (already live; see `minx_mcp/core/server.py:257-266`). The LLM fills `${llm_body}` regions inside a pre-structured frontmatter + heading scaffold; it does not invent structure. This keeps the vault scanner's frontmatter contract stable, makes `vault_replace_section` updates deterministic, and bounds per-run token cost.

### Wiki-page frontmatter contract (coexistence with Slice 6)

Slice 6's vault scanner and reconciler only touch notes with `type: minx-memory`. To guarantee no collision, every LLM-generated wiki page MUST set:

```yaml
type: minx-wiki
wiki_type: entity | pattern | review | goal
```

Verified 2026-04-19: `entity.md`, `pattern.md`, `review.md`, `goal.md` under `minx_mcp/core/templates/wiki/` already encode this. `memory.md` is the Slice 6 memory-note scaffold (`type: minx-memory`) and is out of scope for the wiki playbook. Violating this contract means the memory reconciler will attempt to reconcile an LLM-authored page against the memories table — which is the exact failure mode Slice 6 spent four patches hardening against.

The `daily_review` playbook (§5) writes `Minx/Reviews/YYYY-MM-DD.md` using the same `wiki/review.md` scaffold, so daily review notes and wiki-maintenance-driven review updates share one template. Weekly finance reports continue to use the existing `minx_mcp/finance/templates/finance-weekly-summary.md` and `finance-monthly-summary.md` scaffolds — `finance_generate_weekly_report` is a deterministic SQL-backed render, not an LLM fill.

### Known adjacent risk: reconciler `replace_frontmatter` holds the writer lock

If 8d drives high-frequency vault writes AND those writes trigger cascading memory reconciliation within the same process, the reconciler's single-transaction `replace_frontmatter` call (see `minx_mcp/core/vault_reconciler.py` — flagged in the 2026-04-19 Slice 6 review as an accepted tradeoff) will serialize every other SQLite writer for the duration of each note write. Slice 8d SHOULD NOT trigger `vault_reconcile_memories` synchronously; let the nightly reconcile sweep catch changes instead. If benchmarks show contention, revisit the two-phase reconciler redesign before expanding wiki-maintenance coverage.

## 8) Non-Goals (Slice 8)

- No autonomous actions that modify user data without confirmation (all destructive actions require confirmation gates)
- No multi-step agentic workflows (each playbook is a single script, not a chain). Agent-driven one-shot investigations (`minx_investigate`, `minx_plan`, etc.) are Slice 9's job — see `docs/superpowers/specs/2026-04-19-slice9-agentic-investigations.md`. The audit pattern in §4 (two-phase + reconcile-crashed) is deliberately designed so Slice 9's `investigations` table can lift it directly.
- No harness-specific code inside Core MCP servers
- No notification system in Core (harness decides when/how to notify)
- No APScheduler or scheduling library in Core

## 9) Implementation Phases

| Phase | What | Where | Effort | Dependencies |
|-------|------|-------|--------|-------------|
| 8a | `playbook_runs` table (`019_playbook_runs.sql`) + `start_/complete_/log_playbook_run` + `playbook_history` + `playbook_reconcile_crashed` + `playbook://registry` MCP resource | Core | 2 days | Slice 6a (done) |
| 8b | Wiki template contract audit (`type: minx-wiki` frontmatter in all `minx_mcp/core/templates/wiki/*.md`) + test that reconciler ignores them | Core | 0.5 day | 8a |
| 8c | Daily review + weekly report playbook scripts | Hermes | 2 days | 8a |
| 8d | Wiki maintenance playbook (LLM Wiki pattern) | Hermes | 2-3 days | 8b, 8c, Slice 6e (done) |
| 8e | Confirmation flow for memory candidates + risky actions | Hermes | 1-2 days | 8c, Slice 6a (done) |

**Core effort: 2.5 days**
**Hermes effort: 5-7 days**

## 10) Testing Strategy

### Core tests
- `start_playbook_run` / `complete_playbook_run`: happy path (running → succeeded), condition-miss path (running → skipped), failure path (running → failed), and two-phase crash path (`running` row stays dangling until `playbook_reconcile_crashed` flips it).
- **Concurrency:** two `start_playbook_run` calls with identical `(playbook_id, trigger_type, trigger_ref)` — second must hit the partial unique index and raise `CONFLICT`; no double-insertion.
- `log_playbook_run`: verify it performs an atomic start+complete in a single transaction; no intermediate `running` row visible to concurrent readers.
- `playbook_history`: filter by `playbook_id`, `harness`, `status`, `since`; `limit` enforced; `truncated` flag set when cap hit.
- `playbook_reconcile_crashed`: only flips rows older than `stale_after_minutes`; is idempotent; never touches already-terminal rows.
- Registry resource: every entry has valid namespaced `required_tools` (`server.tool`) and each namespace is known (`core`, `finance`, `meals`, `training`). Core tools in the manifest must resolve against the live Core tool registry.
- Wiki template contract: parse every `minx_mcp/core/templates/wiki/*.md`; assert `type: minx-wiki` in frontmatter; run the memory reconciler against a vault containing one of each and assert `scanned=0` (they're excluded by the scanner's `type` filter).

### Harness tests (Hermes-side)
- Each playbook script: mock Core MCP tool responses, verify correct sequence of calls
- Condition-not-met path: verify `log_playbook_run` called with `conditions_met=False`
- Error path: verify `log_playbook_run` called with `error_message`
- Wiki maintenance: verify LLM is called with correct context, output written to correct paths

## 11) Integration Map — where Slice 8 lands in the current codebase

Everything below is Core-side (8a/8b). Harness-side work (8c/8d/8e) lives outside this repo.

### New files
- `minx_mcp/schema/migrations/019_playbook_runs.sql` — table + indexes from §3.
- `minx_mcp/core/playbooks.py` — new module. Contains `PlaybookDefinition`, `PLAYBOOK_REGISTRY` constant, and pure functions `start_run / complete_run / log_run / query_history / reconcile_crashed`. Mirrors the layout of `minx_mcp/core/vault_reconciler.py`: dataclasses + module-level helpers, no stateful service class unless needed.
- `tests/test_playbook_runs.py` — covers §10 Core tests.
- `tests/test_playbook_registry.py` — asserts every registry entry resolves against the live MCP tool list.
- `tests/test_wiki_template_contract.py` — asserts `type: minx-wiki` frontmatter on all wiki scaffolds and that the reconciler ignores them.

### Modified files
- `minx_mcp/core/server.py` — register six new MCP tools (`start_playbook_run`, `complete_playbook_run`, `log_playbook_run`, `playbook_history`, `playbook_reconcile_crashed`) and one new MCP resource (`playbook://registry`). Slot them next to the existing `memory_*` tools (around line 267) and next to the `wiki-templates://` resource (line 257).
- `minx_mcp/contracts.py` — add `PlaybookConflictError` (maps to an MCP `CONFLICT` error code so the harness can distinguish "already-running" from generic failure) and any input/output TypedDicts worth pinning.
- `minx_mcp/core/templates/wiki/{entity,pattern,review,goal}.md` — already `type: minx-wiki` with `wiki_type: <kind>` as of 2026-04-19. 8b is a verification pass, not an edit. `memory.md` stays `type: minx-memory` (Slice 6 surface).

### Reused, not modified
- `minx_mcp/vault_writer.py`, `minx_mcp/vault_reader.py` — already expose everything 8d needs.
- `minx_mcp/core/memory_service.py` and the `memory_*` tools — confirmation/rejection flow uses them as-is.
- `minx_mcp/finance/report_orchestration.py` — `finance_generate_weekly_report` is already wired.

### Observability
- Structured logs via the existing `logger.info(..., extra={...})` pattern established by `vault_reconciler.py`. Keys: `playbook_id`, `run_id`, `trigger_type`, `status`, `duration_ms`.
- No new metrics backend; `playbook_history` is the monitoring surface.

## 12) Relationship to Other Slices

- **Slice 6 (Memory)**: Slice 8 depends on Slice 6 for `memory_list`, `get_pending_memory_candidates`, and the vault scanner. The wiki maintenance playbook is the primary consumer of memory data for vault writing.
- **Slice 2.5 (MCP Surface Refactor)**: The Core/Harness split in Slice 8 is a direct consequence of the principle established here.
- **Slice 5 (Harness Adaptation)**: Remains deferred. The playbook registry resource is the only harness-facing abstraction needed. If a second harness appears, it reads the same registry and implements its own scheduling.
- **Slice 9 (Agentic Investigations)**: Built on top of Slice 8's audit pattern. Slice 8 = recurring + predictable (scripts); Slice 9 = one-shot + unpredictable (agent loops). Core stays a toolbox in both. See `docs/superpowers/specs/2026-04-19-slice9-agentic-investigations.md`.
