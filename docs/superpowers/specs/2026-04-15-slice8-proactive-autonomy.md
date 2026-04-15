# Slice 8: Proactive Autonomy

**Date:** 2026-04-15  
**Status:** Designed (not yet implemented)  
**Depends on:** Slice 6a (memory CRUD), Consolidation plan (Phases 1-2)  
**Design plan:** `.cursor/plans/slice_6_and_8_design_a8a04289.plan.md`

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

```sql
CREATE TABLE playbook_runs (
    id INTEGER PRIMARY KEY,
    playbook_id TEXT NOT NULL,
    harness TEXT NOT NULL,           -- 'hermes', 'cli', etc.
    triggered_at TEXT NOT NULL,
    trigger_type TEXT NOT NULL,      -- 'cron', 'event', 'manual'
    trigger_ref TEXT,                -- cron expression, event_id, 'manual'
    conditions_met INTEGER NOT NULL, -- 0 or 1
    action_taken INTEGER NOT NULL,   -- 0 or 1
    result_json TEXT,
    error_message TEXT,
    completed_at TEXT
);
```

## 4) MCP Tools (Core server)

### `log_playbook_run`

Called by the harness after executing (or skipping) a playbook. Records the full audit trail.

```
log_playbook_run(
    playbook_id: str,
    harness: str,
    trigger_type: str,          # 'cron' | 'event' | 'manual'
    trigger_ref: str | None,
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
    playbook_id: str | None,
    days: int = 30,
    harness: str | None,
) -> {"runs": [...]}
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

Initial registry entries:

| Playbook ID | Name | Schedule Hint | Required Tools | Confirmation? |
|------------|------|---------------|----------------|---------------|
| `daily_review` | Daily Review | `0 21 * * *` | `get_daily_snapshot`, `persist_note`, `log_playbook_run` | No |
| `weekly_report` | Weekly Finance Report | `0 10 * * 1` | `finance_weekly_report`, `log_playbook_run` | No |
| `wiki_update` | Wiki Maintenance | After daily review | `memory_list`, `get_daily_snapshot`, `persist_note`, `log_playbook_run` | No |
| `memory_review` | Memory Candidate Review | Daily | `get_pending_memory_candidates`, `log_playbook_run` | Yes |
| `goal_nudge` | Goal Check-In Nudge | Daily | `get_daily_snapshot`, `persist_note`, `log_playbook_run` | Yes |

## 6) Condition-Checking Tools (fill gaps)

Most condition data is already available through existing tools. New tools needed:

| Tool | Status | Purpose |
|------|--------|---------|
| `get_daily_snapshot` | Exists | Returns snapshot data including attention items |
| `get_insight_history` | Exists | Historical detector signals |
| `get_pending_memory_candidates` | New (Slice 6) | Candidates awaiting user review |
| `memory_list` | New (Slice 6) | Active memories for wiki page generation |

No additional condition tools are needed beyond what Slice 6 provides.

## 7) Harness Playbook Pattern

Each playbook follows this script pattern on the Hermes side:

```
1. Read playbook definition from registry
2. Check conditions (call Core MCP tools)
3. If conditions not met:
   a. Call log_playbook_run(conditions_met=False, action_taken=False)
   b. Exit
4. If requires_confirmation:
   a. Surface to user in conversation
   b. Wait for approval/rejection
   c. If rejected: log and exit
5. Execute action (call Core MCP tools)
6. Call log_playbook_run(conditions_met=True, action_taken=True, result_json=...)
7. Handle errors: log_playbook_run(error_message=...)
```

### Wiki Maintenance Playbook (LLM Wiki Pattern)

This is the most complex playbook and the primary integration of Karpathy's LLM Wiki concept.

Scheduled after the daily review completes. Steps:

1. Fetch today's snapshot via `get_daily_snapshot`
2. Fetch all active memories via `memory_list(status='active')`
3. For each memory with significant changes since last wiki update:
   a. Generate/update an entity or pattern page using the LLM
   b. Write via `persist_note` (new page) or Core section-update tool (existing page)
   c. Include `[[wikilinks]]` to related entities and patterns
4. Generate/update the daily review summary page at `Minx/Reviews/YYYY-MM-DD.md`
5. Log the run via `log_playbook_run`

The LLM prompt for page generation receives:
- The memory's structured data (from SQLite)
- Recent related insights
- Existing page content (if updating)
- Cross-reference candidates (other memories in the same domain)

## 8) Non-Goals (Slice 8)

- No autonomous actions that modify user data without confirmation (all destructive actions require confirmation gates)
- No multi-step agentic workflows (each playbook is a single script, not a chain)
- No harness-specific code inside Core MCP servers
- No notification system in Core (harness decides when/how to notify)
- No APScheduler or scheduling library in Core

## 9) Implementation Phases

| Phase | What | Where | Effort | Dependencies |
|-------|------|-------|--------|-------------|
| 8a | `playbook_runs` table + `log_playbook_run` + `playbook_history` + registry resource | Core | 1.5 days | Slice 6a |
| 8b | Verify condition-checking tools cover all playbook needs | Core | 0.5 day | 8a |
| 8c | Daily review + weekly report playbook scripts | Hermes | 2 days | 8a, 8b |
| 8d | Wiki maintenance playbook (LLM Wiki pattern) | Hermes | 2-3 days | 8c, Slice 6e |
| 8e | Confirmation flow for memory candidates + risky actions | Hermes | 1-2 days | 8c, Slice 6a |

**Core effort: 2 days**  
**Hermes effort: 5-7 days**

## 10) Testing Strategy

### Core tests
- `log_playbook_run`: Insert and retrieve audit records, verify all fields persisted
- `playbook_history`: Filter by playbook_id, date range, harness
- Registry resource: Verify all playbook definitions are well-formed and serializable

### Harness tests (Hermes-side)
- Each playbook script: mock Core MCP tool responses, verify correct sequence of calls
- Condition-not-met path: verify `log_playbook_run` called with `conditions_met=False`
- Error path: verify `log_playbook_run` called with `error_message`
- Wiki maintenance: verify LLM is called with correct context, output written to correct paths

## 11) Relationship to Other Slices

- **Slice 6 (Memory)**: Slice 8 depends on Slice 6 for `memory_list`, `get_pending_memory_candidates`, and the vault scanner. The wiki maintenance playbook is the primary consumer of memory data for vault writing.
- **Slice 2.5 (MCP Surface Refactor)**: The Core/Harness split in Slice 8 is a direct consequence of the principle established here.
- **Slice 5 (Harness Adaptation)**: Remains deferred. The playbook registry resource is the only harness-facing abstraction needed. If a second harness appears, it reads the same registry and implements its own scheduling.
