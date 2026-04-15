# Slice 6: Durable Memory

**Date:** 2026-04-15  
**Status:** Designed (not yet implemented)  
**Depends on:** Consolidation plan (Phase 1 at minimum), Slices 1-4 foundations  
**Design plan:** `.cursor/plans/slice_6_and_8_design_a8a04289.plan.md`

## 1) Goal

Give Minx persistent memory across sessions so that context, preferences, and learned patterns accumulate over time instead of being re-derived from scratch on every snapshot. The memory system should be queryable by tools, visible to the user through Obsidian, and editable by both Minx and the user.

## 2) Design Principles

- **Queryable**: Memories are structured data in SQLite, not opaque blobs or embeddings-only.
- **Explainable**: Every memory records its source (user-stated, detector-inferred, vault-synced) and has an event log.
- **Auditable**: Memory promotions, rejections, and expirations are tracked in `memory_events`.
- **Human-readable**: Active memories are mirrored as Obsidian wiki pages the user can browse and edit.
- **Bidirectional**: User edits to memory notes in Obsidian are picked up by the vault scanner and synced back to SQLite.

## 3) Architecture: Three Tiers

### Tier 1 — Factual Memory (SQLite)

Structured facts Minx "knows." Directly injected into every snapshot context.

Types:
- `preference`: User-stated preferences ("I'm vegetarian", "payday is the 15th")
- `pattern`: Recurring detected patterns ("coffee at Starbucks every Monday")
- `entity_fact`: Entity knowledge ("Whole Foods is a grocery store")
- `constraint`: Limits and rules ("restaurant budget is $200/week")

### Tier 2 — Episodic Memory (SQLite)

Records of what happened and what Minx saw. Provides reproducibility.

- `snapshot_archives` table: Compressed daily snapshot JSON, one row per review date
- Existing `insights` table: Historical detector output (no changes needed)

### Tier 3 — Vault Wiki (Obsidian, LLM Wiki pattern)

Obsidian vault as a living knowledge surface. Inspired by Karpathy's LLM Wiki concept: the vault is the codebase, the LLM is the programmer, Obsidian is the IDE.

- **Core reads** the vault (vault scanner extracts frontmatter, syncs to `vault_index` table)
- **Harness writes** the vault (LLM generates/updates wiki pages via `persist_note` and section updates)
- User reads and edits the vault in Obsidian (changes sync back on next scan)

## 4) Schema

### `memories` table

```sql
CREATE TABLE memories (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,            -- 'finance', 'meals', 'training', 'core'
    memory_type TEXT NOT NULL,       -- 'preference', 'pattern', 'entity_fact', 'constraint'
    key TEXT NOT NULL,               -- namespaced: 'finance.merchant_category.starbucks'
    value_json TEXT NOT NULL,        -- structured payload
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL,            -- 'user', 'detector', 'vault_sync'
    source_ref TEXT,                 -- detector name, conversation ref, vault path
    status TEXT NOT NULL DEFAULT 'active',  -- 'active', 'candidate', 'expired', 'rejected'
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT,
    UNIQUE(domain, key)
);
```

### `memory_events` table

```sql
CREATE TABLE memory_events (
    id INTEGER PRIMARY KEY,
    memory_id INTEGER NOT NULL REFERENCES memories(id),
    event_type TEXT NOT NULL,        -- 'created', 'promoted', 'confirmed', 'rejected', 'expired', 'updated', 'vault_synced'
    detail_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### `snapshot_archives` table

```sql
CREATE TABLE snapshot_archives (
    id INTEGER PRIMARY KEY,
    review_date TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### `vault_index` table

```sql
CREATE TABLE vault_index (
    id INTEGER PRIMARY KEY,
    vault_path TEXT NOT NULL UNIQUE,
    note_type TEXT,                  -- from frontmatter: 'minx-memory', 'minx-entity', etc.
    domain TEXT,                     -- from frontmatter
    content_hash TEXT NOT NULL,      -- detect changes on rescan
    last_scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata_json TEXT               -- extracted frontmatter fields
);
```

## 5) MCP Tools (Core server)

| Tool | Purpose |
|------|---------|
| `memory_list(domain?, memory_type?, status?)` | Browse memories with filters |
| `memory_get(memory_id)` | Single memory with event history |
| `memory_create(domain, memory_type, key, value_json, source?)` | Manual creation (harness records user-stated preferences) |
| `memory_confirm(memory_id)` | Promote candidate to active |
| `memory_reject(memory_id)` | Reject a candidate |
| `memory_expire(memory_id)` | Manually expire |
| `get_pending_memory_candidates(domain?)` | Candidates awaiting confirmation (harness uses this) |

## 6) New Detectors

These run during snapshot building and propose candidate memories.

### `detect_recurring_merchant_pattern`
- **Trigger**: Merchant appears 4+ times in 30 days with similar amounts (within 20% of median)
- **Output**: Propose `pattern` memory with key `finance.recurring.{merchant_normalized}`
- **Confidence**: 0.7 base, +0.1 for each additional occurrence beyond 4, cap at 0.95

### `detect_category_preference`
- **Trigger**: User has manually categorized a merchant the same way 3+ times
- **Output**: Propose `entity_fact` memory with key `finance.merchant_category.{merchant_normalized}`
- **Confidence**: 0.9 (user-confirmed behavior)

### `detect_schedule_pattern`
- **Trigger**: Activity (workout, meal type) occurs on the same weekday(s) for 3+ consecutive weeks
- **Output**: Propose `pattern` memory with key `{domain}.schedule.{activity_type}`
- **Confidence**: 0.75 base, increases with consistency

## 7) Memory Promotion Pipeline

1. Detectors propose memories with `status='candidate'` and a confidence score
2. If `confidence >= 0.8`: auto-promote to `status='active'`, log `memory_event('promoted')`
3. If `confidence < 0.8`: stays as candidate, included in `get_pending_memory_candidates`
4. Harness surfaces candidates to user in conversation (via snapshot attention items or dedicated review flow)
5. User confirms (`memory_confirm`) or rejects (`memory_reject`)

## 8) Vault Scanner

Extends the pattern from `meals/service.py` recipe indexing to a general-purpose scanner.

Module: `minx_mcp/core/vault_scanner.py`

Behavior:
1. Walk the configured vault path looking for `.md` files under `Minx/` folders
2. Parse YAML frontmatter for `type`, `domain`, and other Minx-specific fields
3. Hash file content; skip files that haven't changed since last scan
4. Upsert `vault_index` table with extracted metadata
5. For notes with `type: minx-memory`: sync back to `memories` table (create or update), log `vault_synced` event

Frontmatter contract for Minx-managed notes:
```yaml
---
type: minx-memory          # or minx-entity, minx-pattern, etc.
domain: finance
memory_key: finance.merchant_category.starbucks
updated: 2026-04-15
---
```

## 9) Vault Wiki Pages (Harness-Generated)

The harness (Hermes) generates and maintains wiki pages using the LLM. These are written via Core's `persist_note` tool. Core does not generate prose.

| Page type | Path pattern | Content |
|-----------|-------------|---------|
| Entity pages | `Minx/Entities/Starbucks.md` | Category, typical spend, frequency, linked goals |
| Pattern pages | `Minx/Patterns/Weekly-Spending.md` | Recurring patterns with cross-links |
| Review summaries | `Minx/Reviews/2026-04-15.md` | Daily snapshot summary with wikilinks |
| Goal pages | `Minx/Goals/Restaurant-Budget.md` | Goal context, progress history, related entities |

All pages use `[[wikilinks]]` for Obsidian cross-referencing.

## 10) Integration Points

- **Snapshot builder** (`read_models.py`): New `MemoryContext` field on `ReadModels` containing active memories, pending candidates count, and recent memory events
- **Goal system**: Goals can reference memories for context enrichment
- **Finance query**: LLM interpretation layer uses memories for disambiguation
- **VaultWriter**: `replace_section` method (already tested, first production use case) enables targeted section updates in wiki pages

## 11) Non-Goals (Slice 6)

- No autonomous LLM-driven memory creation (LLM Wiki maintenance is a harness concern in Slice 8)
- No vector embeddings in the initial build (deferred to 6g)
- No cross-vault sync (single Obsidian vault assumed)
- No memory versioning beyond the event log

## 12) Implementation Phases

| Phase | What | Effort | Dependencies |
|-------|------|--------|-------------|
| 6a | Memory schema + MemoryService + CRUD MCP tools + first detectors | 2-3 days | Consolidation plan done |
| 6b | Snapshot archive table + auto-persist on snapshot build | 1-2 days | 6a |
| 6c | Vault scanner (frontmatter indexing) | 1-2 days | 6a |
| 6d | Inject MemoryContext into ReadModels and snapshot builder | 1 day | 6a |
| 6e | Wiki page generation for memories (vault write-back, harness side) | 1 day | 6a, 6c |
| 6f | Bidirectional vault sync (user edits -> memory updates) | 1 day | 6c, 6e |
| 6g (optional) | Semantic search with sqlite-vec | 2-3 days | 6c proven insufficient |

**Total Core effort: 8-11 days**

## 13) Testing Strategy

- Unit tests for `MemoryService` CRUD operations and event logging
- Detector tests with fixture data (e.g., seed 10 Starbucks transactions, assert candidate proposed)
- Vault scanner tests with temp directory containing markdown files with frontmatter
- Integration test: propose memory -> auto-promote -> verify in snapshot context
- Integration test: write memory note -> manually edit frontmatter -> rescan -> verify memory updated
- Snapshot archive roundtrip: build snapshot -> archive -> retrieve -> compare
