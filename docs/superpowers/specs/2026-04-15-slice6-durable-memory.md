# Slice 6: Durable Memory

**Date:** 2026-04-15  
**Status:** Phases 6a + 6b shipped 2026-04-17; 6c–6f pending  
**Depends on:** Consolidation plan (Phase 1 at minimum), Slices 1-4 foundations  
**Design plan:** `docs/superpowers/plans/2026-04-15-slice6-and-8-design.plan.md`

> **Note on drift from this spec.** The shipped schema (migrations `013_slice6_memory.sql`
> and `014_slice6_snapshot_archives.sql`) renamed `domain`/`key`/`value_json` to
> `scope`/`subject`/`payload_json`, added columns (`reason`, `last_confirmed_at`), and
> expanded `snapshot_archives` to include `generated_at`, `content_hash`, and `source`
> with `UNIQUE(review_date, content_hash)` in place of `UNIQUE(review_date)`. DDL
> snippets below are the **as-shipped** schema; the MCP tool table reflects the
> **as-shipped** signatures. Detector thresholds and windows were tuned during
> implementation — see module docstrings in `minx_mcp/core/memory_detectors.py` for
> the canonical numbers.

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

Structured facts Minx "knows." As of phase 6a these are persisted and queryable via MCP tools; phase 6d will expose them on `DailySnapshot.memory_context` so every snapshot carries the live memory corpus without a second round-trip.

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

### `memories` table (as shipped — migration 013)

```sql
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_type TEXT NOT NULL,       -- 'preference', 'pattern', 'entity_fact', 'constraint', ...
    scope TEXT NOT NULL,             -- 'finance', 'meals', 'training', 'core' (a.k.a. "domain")
    subject TEXT NOT NULL,           -- slugified identifier within scope, e.g. 'starbucks'
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    status TEXT NOT NULL DEFAULT 'candidate'
        CHECK(status IN ('candidate', 'active', 'rejected', 'expired')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL,            -- e.g. 'user', 'detector:recurring_merchant', 'vault_sync'
    reason TEXT NOT NULL DEFAULT '', -- latest rationale string from the writer
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_confirmed_at TEXT,
    expires_at TEXT
);

-- Added in migration 015: partial uniqueness guards against concurrent
-- detectors inserting duplicate live rows. Terminal (rejected/expired)
-- rows remain as history and do not participate in the constraint.
CREATE UNIQUE INDEX uq_memories_live_triple
    ON memories(memory_type, scope, subject)
    WHERE status IN ('candidate', 'active');
```

### `memory_events` table (as shipped — migration 013)

```sql
CREATE TABLE memory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK(
        event_type IN (
            'created', 'promoted', 'confirmed', 'rejected', 'expired',
            'payload_updated', 'vault_synced', 'reopened'
        )
    ),
    payload_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT 'system'
        CHECK(actor IN ('system', 'detector', 'user', 'harness', 'vault_sync')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### `snapshot_archives` table (as shipped — migration 014)

```sql
CREATE TABLE snapshot_archives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_date TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    snapshot_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,      -- SHA-256 over stable-serialized snapshot_json
    source TEXT NOT NULL DEFAULT 'build_daily_snapshot',
    UNIQUE(review_date, content_hash)  -- dedupe identical rebuilds; divergent rebuilds still append
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


| Tool                                                                               | Purpose                                                                  |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `memory_list(status?, memory_type?, scope?, limit?)`                               | Browse memories with filters                                             |
| `memory_get(memory_id)`                                                            | Single memory                                                            |
| `memory_create(memory_type, scope, subject, confidence, payload, source, reason?)` | Manual creation (harness records user-stated preferences)                |
| `memory_confirm(memory_id)`                                                        | Promote candidate to active (candidate-only)                             |
| `memory_reject(memory_id, reason?)`                                                | Reject a candidate (candidate-only; use `memory_expire` for active rows) |
| `memory_expire(memory_id, reason?)`                                                | Manually expire an active memory (active-only; idempotent on already-expired) |
| `get_pending_memory_candidates(scope?, limit?)`                                    | Candidates awaiting confirmation (harness uses this)                     |
| `list_snapshot_archives(review_date?, limit?)`                                     | Metadata listing of archived snapshots                                   |
| `get_snapshot_archive(archive_id)`                                                 | Full archived snapshot JSON                                              |


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


| Page type        | Path pattern                       | Content                                          |
| ---------------- | ---------------------------------- | ------------------------------------------------ |
| Entity pages     | `Minx/Entities/Starbucks.md`       | Category, typical spend, frequency, linked goals |
| Pattern pages    | `Minx/Patterns/Weekly-Spending.md` | Recurring patterns with cross-links              |
| Review summaries | `Minx/Reviews/2026-04-15.md`       | Daily snapshot summary with wikilinks            |
| Goal pages       | `Minx/Goals/Restaurant-Budget.md`  | Goal context, progress history, related entities |


All pages use `[[wikilinks]]` for Obsidian cross-referencing.

### Template scaffolds (ship with phase 6e)

Even though prose is LLM-generated, each page type has a **fixed structural contract** the vault scanner and `replace_section` flow must be able to parse. Ship a scaffold template per page type that the harness fills in via LLM. This:

1. Pins the YAML frontmatter block (`type: minx-memory`, `domain`, `memory_key`, `updated`) so the vault scanner's frontmatter contract is always valid — no brittle LLM-invented structure.
2. Pins section headings (`## Summary`, `## Linked Memories`, `## History`, etc.) so `VaultWriter.replace_section` targets are deterministic across runs and the bidirectional sync in phase 6f can round-trip user edits without structural drift.
3. Bounds LLM output, reducing token cost and eliminating "reinvent the layout" noise.

Where the templates live and how they're served:

- **Location:** `minx_mcp/core/templates/wiki/` — ship alongside Core as package data so they travel with the wheel and stay versioned with the scanner/writer contract. Follow the pattern already established by `minx_mcp/finance/templates/` (PEP 503 package data, asserted packed in `tests/test_db.py::test_built_wheel_includes_packaged_resources`).
- **Discovery (optional):** Publish a `template://wiki/{page_type}` MCP resource so any harness — not just Hermes — can read the canonical scaffolds. This mirrors the `playbook://registry` pattern Slice 8 adopts.
- **Fill semantics:** Templates use `string.Template` `${placeholder}` syntax (same as finance report templates) for deterministic fields (memory_key, updated timestamp, domain). Free-form body sections are left as `## Heading\n${llm_body}` so the LLM fills bounded regions.

Required scaffolds for phase 6e:

- `wiki/entity.md` — entity pages (Entities/)
- `wiki/pattern.md` — pattern pages (Patterns/)
- `wiki/review.md` — daily review summary (Reviews/YYYY-MM-DD.md); also consumed by Slice 8's `daily_review` playbook
- `wiki/goal.md` — goal context pages (Goals/)

## 10) Integration Points

- **Read models**: Add `MemoryContext` to the `ReadModels` dataclass in `minx_mcp/core/snapshot_models.py`; populate it inside `minx_mcp/core/read_models.py::build_read_models`. It holds active memories, pending candidates count, and recent memory events.
- **Goal system**: Goals can reference memories for context enrichment
- **Finance query**: LLM interpretation layer uses memories for disambiguation
- **VaultWriter**: `replace_section` method (already tested, first production use case) enables targeted section updates in wiki pages

## 11) Non-Goals (Slice 6)

- No autonomous LLM-driven memory creation (LLM Wiki maintenance is a harness concern in Slice 8)
- No vector embeddings in the initial build (deferred to 6g)
- No cross-vault sync (single Obsidian vault assumed)
- No memory versioning beyond the event log

## 12) Implementation Phases


| Phase         | What                                                                                                                                | Effort   | Dependencies            |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------- | -------- | ----------------------- |
| 6a            | Memory schema + MemoryService + CRUD MCP tools + first detectors                                                                    | 2-3 days | Consolidation plan done |
| 6b            | Snapshot archive table + auto-persist on snapshot build                                                                             | 1-2 days | 6a                      |
| 6c            | Vault scanner (frontmatter indexing)                                                                                                | 1-2 days | 6a                      |
| 6d            | Add `MemoryContext` on `ReadModels` in `snapshot_models.py`, populate in `read_models.py::build_read_models`, wire snapshot builder | 1 day    | 6a                      |
| 6e            | Wiki page generation for memories + template scaffolds at `minx_mcp/core/templates/wiki/` (entity/pattern/review/goal) + optional `template://wiki/{page_type}` MCP resource | 1-2 days | 6a, 6c                  |
| 6f            | Bidirectional vault sync (user edits -> memory updates)                                                                             | 1 day    | 6c, 6e                  |
| 6g (optional) | Semantic search with sqlite-vec                                                                                                     | 2-3 days | 6c proven insufficient  |


**Total Core effort: 8-11 days**

## 13) Testing Strategy

- Unit tests for `MemoryService` CRUD operations and event logging
- Detector tests with fixture data (e.g., seed 10 Starbucks transactions, assert candidate proposed)
- Vault scanner tests with temp directory containing markdown files with frontmatter
- Integration test: propose memory -> auto-promote -> verify in snapshot context
- Integration test: write memory note -> manually edit frontmatter -> rescan -> verify memory updated
- Snapshot archive roundtrip: build snapshot -> archive -> retrieve -> compare

## Implementation notes (post-review)

- 2026-04-17: clarified that `MemoryContext` lives in `snapshot_models.py` (the `ReadModels` home), populated by `read_models.py::build_read_models`.
- 2026-04-17: after initial post-merge review, `MemoryService.ingest_proposals` was
tightened to **silently suppress proposals whose prior row is `rejected`** (detectors
must not re-surface user-rejected memories) and to treat `expired` as a fresh
lifecycle (insert a new row). `reject_memory` was restricted to `candidate` status;
`memory_expire` is the path for removing an active memory.
- 2026-04-17: schema column naming diverged from this spec: `domain` → `scope`,
`key` → `subject`, `value_json` → `payload_json`. Tool signatures follow the shipped
column names; "domain" survives only as a conversational alias in docs.
- 2026-04-17: partial `UNIQUE(memory_type, scope, subject) WHERE status IN ('candidate', 'active')`
index added (migration 015) to enforce at-most-one-live-row per triple at the DB
level, complementing the application-level dedupe in `ingest_proposals`.
- 2026-04-17: post-6b code review tightened two correctness rails:
  1. `expire_memory` now **only accepts `active` rows**. Previously it also accepted
     `candidate` and (silently) `rejected` rows, which allowed a detector to
     effectively resurrect a user-rejected memory by going
     rejected → expired → next ingest treats "expired prior" as a new lifecycle
     → fresh candidate row. Terminal states (`rejected`, `expired`) are now
     sticky by design.
  2. `create_memory` translates the migration-015 partial-unique-index violation
     into a `CONFLICT` error (with the offending `(memory_type, scope, subject)`
     triple in `data`) instead of bubbling `sqlite3.IntegrityError` up as
     `INTERNAL_ERROR`. MCP clients can now distinguish "duplicate live memory"
     from generic internal failures.

