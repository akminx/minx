# Slice 6 Durable Memory: Implementation Design

**Date:** 2026-04-17
**Status:** Approved for implementation planning
**Depends on:** Slices 1-4 foundations, consolidation/code-quality hardening
**Supersedes:** Broad Slice 6 design details in `2026-04-15-slice6-durable-memory.md` where this document is more specific

## 1) Goal

Give Minx durable, queryable memory across sessions without turning Core into an autonomous prose generator. Core owns structured memory storage, memory lifecycle rules, detector proposals, snapshot context, vault scanning, and audit history. Hermes or another harness owns conversation, confirmations, scheduling, and LLM-authored wiki maintenance.

## 2) Governing Rule

Keep the existing Core/Harness split:

- **Core:** data, deterministic logic, validation, audit trails, MCP tools, and read models.
- **Harness:** narrative, notification policy, confirmation conversations, scheduling, and LLM wiki writing.

No scheduling library, autonomous LLM workflow, or harness-specific orchestration belongs in Slice 6 Core code.

## 3) Key Design Decision: Canonical Memory Keys

The `memories` table keeps `UNIQUE(domain, key)`.

A memory key represents the current canonical knowledge slot for that fact or pattern. Historical lifecycle changes are stored in `memory_events`; they are not represented by duplicate `memories` rows.

### Statuses

- `candidate`: proposed but not yet trusted.
- `active`: trusted and eligible for snapshot context.
- `rejected`: user or harness rejected the proposal.
- `expired`: previously useful, no longer assumed true.

### Create vs Propose

Two write paths are required:

1. `create_memory(...)`
   - Used for user-stated or vault-synced facts.
   - Defaults to `active` unless the caller explicitly chooses `candidate`.
   - May update an existing row with the same `domain + key`.

2. `propose_memory(...)`
   - Used by detectors.
   - Applies confidence thresholds and revival rules.
   - Never creates duplicate rows for the same `domain + key`.

### Revival Rules

When `propose_memory(...)` finds an existing `rejected` or `expired` row, it may reopen that row as `candidate` only if at least one condition is true:

- New confidence is at least `0.10` higher than the stored confidence.
- Proposed `value_json` materially differs from the stored `value_json`.
- The proposal evidence window is newer than the prior proposal evidence window.
- Cooldown has passed:
  - `rejected`: 30 days
  - `expired`: 14 days
- Source strength increases:
  - detector < vault_sync < user

If none of those conditions are true, the proposal is suppressed and the existing row remains unchanged. Slice 6a does not write `suppressed` events by default, because unchanged detector proposals may recur daily and would create audit noise without a user-visible lifecycle transition.

### Auto-Promotion

Detector proposals with confidence `>= 0.80` become `active` immediately unless an existing row is `rejected` and revival was blocked. Detector proposals below `0.80` remain `candidate`.

Manual/user and vault-sync writes may create `active` rows directly because they represent stronger evidence than a detector inference.

## 4) Schema

Add migration `013_memory.sql` to both migration mirrors:

- `minx_mcp/schema/migrations/013_memory.sql`
- `schema/migrations/013_memory.sql`

Tables:

```sql
CREATE TABLE memories (
    id INTEGER PRIMARY KEY,
    domain TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL,
    source_ref TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    last_proposed_at TEXT,
    last_evidence_start TEXT,
    last_evidence_end TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT,
    UNIQUE(domain, key)
);
```

```sql
CREATE TABLE memory_events (
    id INTEGER PRIMARY KEY,
    memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

```sql
CREATE TABLE snapshot_archives (
    id INTEGER PRIMARY KEY,
    review_date TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

```sql
CREATE TABLE vault_index (
    id INTEGER PRIMARY KEY,
    vault_path TEXT NOT NULL UNIQUE,
    note_type TEXT,
    domain TEXT,
    content_hash TEXT NOT NULL,
    last_scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
```

Indexes:

- `idx_memories_status_domain` on `(status, domain)`
- `idx_memories_type_status` on `(memory_type, status)`
- `idx_memory_events_memory_created` on `(memory_id, created_at)`
- `idx_snapshot_archives_review_date` on `(review_date)`
- `idx_vault_index_type_domain` on `(note_type, domain)`

## 5) Validation Contract

Valid domains:

- `core`
- `finance`
- `meals`
- `training`

Valid memory types:

- `preference`
- `pattern`
- `entity_fact`
- `constraint`

Valid sources:

- `user`
- `detector`
- `vault_sync`
- `manual`

Valid statuses:

- `candidate`
- `active`
- `rejected`
- `expired`

Key rules:

- `key` must be non-empty, lowercase, dot-delimited, and start with the domain prefix.
- `value_json` must parse as a JSON object.
- `confidence` must be between `0.0` and `1.0`.
- Evidence dates, when present, must be ISO dates and `last_evidence_end >= last_evidence_start`.
- `expires_at`, when present, must be an ISO timestamp or ISO date accepted by existing validation helpers.

## 6) Proposed Package Shape

Create:

- `minx_mcp/core/memory_models.py`
- `minx_mcp/core/memory_service.py`
- `minx_mcp/core/memory_detectors.py`
- `minx_mcp/core/vault_scanner.py`

Modify:

- `minx_mcp/core/server.py`
- `minx_mcp/core/snapshot_models.py`
- `minx_mcp/core/models.py`
- `minx_mcp/core/read_models.py`
- `minx_mcp/core/snapshot.py`
- `minx_mcp/db.py` only if migration helper changes become necessary

Tests:

- `tests/test_memory_service.py`
- `tests/test_memory_server.py`
- `tests/test_memory_detectors.py`
- `tests/test_memory_snapshot.py`
- `tests/test_vault_scanner.py`
- Extend `tests/test_migration_checksums.py` only if mirror coverage needs a new assertion.

## 7) Service API

`MemoryService` should accept an injected SQLite `Connection`, matching `GoalService`.

Required methods:

- `create_memory(payload: MemoryCreateInput) -> MemoryRecord`
- `propose_memory(payload: MemoryProposalInput) -> MemoryProposalResult`
- `get_memory(memory_id: int) -> MemoryWithEvents`
- `list_memories(domain: str | None = None, memory_type: str | None = None, status: str | None = None) -> list[MemoryRecord]`
- `confirm_memory(memory_id: int) -> MemoryRecord`
- `reject_memory(memory_id: int, reason: str | None = None) -> MemoryRecord`
- `expire_memory(memory_id: int, reason: str | None = None) -> MemoryRecord`
- `get_pending_memory_candidates(domain: str | None = None) -> list[MemoryRecord]`
- `list_active_context(domain: str | None = None, limit: int = 50) -> list[MemoryRecord]`

`MemoryProposalResult` should include:

- `memory`: the canonical row
- `action`: `created`, `updated`, `promoted`, `revived`, or `suppressed`
- `reason`: machine-readable explanation for the decision

All multi-row operations must use savepoints and commit only after both the `memories` row and `memory_events` row are written.

## 8) MCP Tool Surface

Add Core tools:

- `memory_list(domain=None, memory_type=None, status=None)`
- `memory_get(memory_id)`
- `memory_create(domain, memory_type, key, value_json, source='manual', source_ref=None, status='active', confidence=1.0)`
- `memory_confirm(memory_id)`
- `memory_reject(memory_id, reason=None)`
- `memory_expire(memory_id, reason=None)`
- `get_pending_memory_candidates(domain=None)`

Do not expose `propose_memory` as a public MCP tool in 6a. It is an internal detector/service method so detectors cannot bypass the lifecycle policy.

Tool responses must follow the existing `ToolResponse` wrapping style and return JSON-serializable dictionaries, not dataclass instances.

## 9) Snapshot Integration

Add `MemoryContext` to `snapshot_models.py`:

- `active`: list of compact memory DTOs
- `pending_candidate_count`: integer
- `recent_events`: list of compact event DTOs

Add `memory_context` to `ReadModels` and `DailySnapshot`.

Snapshot behavior:

- Active memories are included in snapshot context for Core/Hermes consumption.
- Candidate values are not injected as facts.
- `pending_candidate_count > 0` adds an attention item such as `3 memory candidates need review.`
- Memory context failures should follow the existing persistence-warning philosophy: the daily snapshot should remain useful when optional durability or context enrichment fails.

## 10) Snapshot Archives

After `build_daily_snapshot(...)` creates the final `DailySnapshot`, persist its JSON representation into `snapshot_archives` keyed by `review_date`.

Rules:

- Default behavior upserts by `review_date`.
- `force=True` replaces the stored archive for that date.
- Archive persistence failure should produce a `persistence_warning` rather than failing the whole snapshot.
- Do not expose an MCP archive retrieval tool in Slice 6. Tests should validate archive persistence through service/helper code or direct database assertions.

## 11) First Memory Detectors

Implement memory detectors separately from insight detectors to avoid mixing user-facing alerts with durable knowledge proposals.

Initial detectors:

1. `detect_recurring_merchant_pattern`
   - Domain: `finance`
   - Type: `pattern`
   - Key: `finance.recurring.<merchant_slug>`
   - Trigger: merchant appears 4+ times in 30 days with amounts within 20% of median.
   - Confidence: `0.70 + 0.05` for each occurrence beyond 4, capped at `0.95`.
   - Evidence window: 30 days ending on review date.

2. `detect_category_preference`
   - Domain: `finance`
   - Type: `entity_fact`
   - Key: `finance.merchant_category.<merchant_slug>`
   - Trigger: same merchant/category pairing appears at least 3 times.
   - Confidence: `0.90`.
   - Evidence window: 90 days ending on review date.

Defer `detect_schedule_pattern` until finance memory proposals are proven, because meals/training schedule semantics need more product choices.

## 12) Vault Scanner

Add `minx_mcp/core/vault_scanner.py`.

Behavior:

1. Walk the configured vault path under the `Minx/` folder.
2. Read `.md` files.
3. Parse YAML-like frontmatter for simple scalar fields.
4. Hash full file contents.
5. Skip unchanged files by comparing against `vault_index.content_hash`.
6. Upsert `vault_index`.
7. For `type: minx-memory`, sync to `memories` via `MemoryService.create_memory(..., source='vault_sync')`.

Frontmatter contract:

```yaml
---
type: minx-memory
domain: finance
memory_type: entity_fact
memory_key: finance.merchant_category.starbucks
confidence: 1.0
status: active
value_json: {"category": "Coffee Shops", "merchant": "Starbucks"}
---
```

Value extraction:

- If frontmatter includes `value_json`, parse that value as the memory payload.
- If frontmatter omits `value_json`, use `{"body": "<markdown body without frontmatter>"}`.
- If `value_json` is present but invalid JSON, index the note in `vault_index` and skip memory sync for that note.

The scanner must not generate prose. It only indexes and syncs structured fields.

## 13) Non-Goals

- No vector embeddings.
- No semantic search.
- No autonomous LLM memory creation.
- No Core scheduler.
- No Hermes cron/playbook implementation.
- No destructive vault rewrites.
- No bidirectional sync beyond `type: minx-memory` frontmatter and body-derived value in the first scanner pass.

## 14) Implementation Phases

### 6a: Memory foundation

- Migration.
- Models.
- `MemoryService`.
- Core MCP memory tools.
- Unique-key lifecycle and revival semantics.
- Tests for CRUD, events, lifecycle, MCP wrapping, and migration mirrors.

### 6b: Snapshot archives

- `snapshot_archives` persistence.
- Warning behavior on archive failure.
- Tests for upsert and `force=True`.

### 6c: Snapshot memory context

- `MemoryContext` models.
- Read-model and snapshot integration.
- Attention item for pending candidates.
- Tests for active-only context injection.

### 6d: Finance memory detectors

- `memory_detectors.py`.
- Recurring merchant and category preference proposals.
- Service-level proposal calls with auto-promotion/revival behavior.
- Tests for proposal creation, suppression, revival, and promotion.

### 6e: Vault scanner

- `vault_index`.
- Frontmatter parser.
- Memory sync from `Minx/` notes.
- Tests with temporary vault directories.

## 15) Acceptance Criteria

- `UNIQUE(domain, key)` remains in place.
- Rejected memories suppress identical future detector proposals.
- Rejected memories revive when confidence, evidence, value, source strength, or cooldown rules justify it.
- Expired memories can revive with a shorter cooldown than rejected memories.
- Every lifecycle transition writes a `memory_events` row.
- Active memories appear in daily snapshot memory context.
- Candidates do not appear as active facts.
- Pending candidates produce a snapshot attention item.
- Snapshot archives persist without breaking snapshot generation when archive persistence fails.
- Vault scanner indexes changed `Minx/*.md` files and syncs `type: minx-memory` notes.
- Full verification remains green:
  - `uv run pytest -q`
  - `uv run mypy`
  - `uv run ruff check .`

## 16) Open Decisions Deferred

- Whether memory wiki pages should have a dedicated section-update MCP tool. Existing `VaultWriter.replace_section` supports the operation, but exposing it can wait for Slice 8/Hermes wiki maintenance.
- Whether `memory_get` should accept `domain + key` as an alternative lookup. Start with `memory_id` only to keep the MCP contract small.
- Whether schedule-pattern memories should be finance/meals/training-generic or domain-specific. Defer until after finance memory proposals are working.
