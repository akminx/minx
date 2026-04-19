# Slice 6c-6f: Vault Scanner, Memory Context, Wiki Primitives, Vault Reconciliation

**Date:** 2026-04-18
**Status:** Revised after Slice 6c implementation, review, and 6d-6f usability pass
**Depends on:** Slice 6a-6b shipped: durable memory schema/tools, memory events, snapshot archives
**Related docs:**
- `docs/superpowers/specs/2026-04-15-slice6-durable-memory.md`
- `docs/superpowers/specs/2026-04-17-slice6-memory-implementation-design.md`
- `docs/superpowers/plans/2026-04-17-slice6c-vault-scanner.md`

## 1) Goal

Finish the durable memory loop without moving narrative or scheduling logic into Core:

- **6c:** Maintain `vault_index` and sync valid `minx-memory` notes from vault to SQLite.
- **6d:** Add deterministic `MemoryContext` DTOs to daily snapshots for harness context.
- **6e:** Serve wiki templates and expose deterministic vault section-update primitives.
- **6f:** Reconcile user vault edits back into SQLite with explicit identity, conflict, and terminal-state rules.

The completed system should preserve the Slice 6 three-tier memory model:

- **Tier 1 factual memory:** structured, validated rows in SQLite.
- **Tier 2 episodic memory:** snapshot archives and memory events in SQLite.
- **Tier 3 vault wiki:** human-readable Obsidian pages that are safe for Minx/Hermes to maintain incrementally.

Tier 3 is human-readable first and machine-syncable second. Normal users should not need to edit JSON or sync timestamps for everyday use; those fields exist as deterministic metadata rails. In Slice 6f, Core only syncs structured frontmatter. Free-form prose-to-memory extraction remains a Hermes/user-confirmed future behavior, not a silent Core behavior.

## 2) Boundary

Core owns deterministic storage, validation, migration, scanning, template resources, and section replacement. Core may read and write markdown only through bounded tools with path validation.

Hermes owns cadence, scheduling, prose, narrative judgement, user confirmation flows, and deciding when to call Core tools. Core must not generate wiki prose or silently schedule background sync.

## 3) Vault Path Contract

Core vault operations are rooted at `settings.vault_path` and restricted to allowed vault prefixes. For Slice 6 memory/wiki operations, the allowed prefix is `Minx/`.

All paths are vault-relative POSIX paths. Absolute paths, `..` escapes, and symlinks resolving outside the vault root are rejected by `VaultReader` and `VaultWriter`.

## 4) Canonical Memory Note Frontmatter

For notes that sync into `memories`, canonical frontmatter is:

```yaml
---
type: minx-memory
scope: core
memory_key: core.preference.timezone
memory_type: preference
subject: timezone
memory_id: 123
sync_base_updated_at: "2026-04-18 10:15:00"
payload_json: {"category": "timezone", "value": "America/Chicago"}
---
```

Canonical fields:

- `type`: must be `minx-memory`.
- `scope`: canonical memory scope. `domain` is accepted as a legacy alias during 6c/6f, but new templates should write `scope`.
- `memory_key`: required stable identity in the format `{scope}.{memory_type}.{subject}`.
- `memory_type`: must match the `memory_key` middle segment.
- `subject`: optional mirror. If present, it must match the `memory_key` subject segment.
- `memory_id`: optional but preferred for notes generated from an existing memory row. When present in 6f, it is authoritative and must identify the same `(scope, memory_type, subject)` as `memory_key`.
- `sync_base_updated_at`: required for Core/Hermes-generated memory notes in 6f conflict detection. It records the **exact SQLite `memories.updated_at` string** observed when the note was generated or last reconciled. Reconciliation compares this string exactly; implementations must not reformat it before comparison.
- `payload_json`: canonical payload carrier for 6f. It must decode to a JSON object and pass the memory payload validator for the declared `memory_type`.

Canonical memory notes should also have a human-facing body. The body gives the user a safe place to read and edit prose without touching machine metadata:

```markdown
# Timezone Preference

## Summary
Use America/Chicago unless I say otherwise.

## Human Editable
My timezone is America/Chicago.

## System Metadata
The YAML frontmatter above is used for sync. Normal edits should happen in Human Editable.
```

Core does not infer structured payloads from body prose in 6f. Hermes may read the human-editable section and propose a structured memory change through an explicit user confirmation flow, but that is outside Core's deterministic reconciliation contract.

6c compatibility:

- 6c may also derive payload from non-reserved frontmatter keys to support early vault-authored notes.
- Reserved keys are `type`, `scope`, `domain`, `memory_key`, `memory_type`, `subject`, `memory_id`, `updated`, `sync_base_updated_at`, `payload_json`, and `value_json`.
- `value_json` is a temporary legacy alias for `payload_json`.

Frontmatter parsing note: the custom frontmatter parser in `VaultReader` returns JSON-object values (e.g. `payload_json: {"a": "b"}`) as raw strings, not dicts. Scanner/reconciler code must `json.loads` them explicitly before validating as a payload; `_parse_memory_payload` already handles both dict and string forms. Consumers that read `vault_index.metadata_json` should be aware that `payload_json` within it is stored as an embedded JSON string rather than a nested object — this is cosmetic but worth knowing when querying the index.

## 5) Slice 6c: Vault Scanner

### 5.1 Schema

Migration `018_vault_index.sql` creates:

```sql
CREATE TABLE vault_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_path TEXT NOT NULL UNIQUE,
    note_type TEXT,
    scope TEXT,
    content_hash TEXT NOT NULL,
    last_scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    memory_id INTEGER REFERENCES memories(id) ON DELETE SET NULL
);
```

Indexes:

- `idx_vault_index_note_type`
- `idx_vault_index_memory_id`
- `idx_vault_index_last_scanned_at`

Migration `018` also rebuilds `memory_events` so `event_type='vault_synced'` is valid again.

### 5.2 Scanner API

`minx_mcp/core/vault_scanner.py` defines:

```python
@dataclass(frozen=True)
class VaultScanReport:
    scanned: int
    indexed: int
    updated: int
    unchanged: int
    orphaned: int
    memory_syncs: int
    warnings: list[str]

class VaultScanner:
    def scan(self, *, dry_run: bool = False) -> VaultScanReport: ...
```

### 5.3 Scanner Behavior

The scanner:

1. Walks `Minx/` in two phases, both routed through `VaultReader` (no direct filesystem access):
   - Phase 1a: enumerate markdown paths via `VaultReader.iter_markdown_paths("Minx")`.
   - Phase 1b: read each path via `VaultReader.read_document`. A per-file read failure (invalid UTF-8, malformed frontmatter, etc.) emits a warning and skips that file but does **not** mark the walk incomplete. Only a failure from path enumeration itself fails the walk and suppresses orphan cleanup.
2. Indexes every readable markdown note into `vault_index`.
3. Uses `content_hash` as the fast path. Hash match updates only `last_scanned_at`.
4. Upserts changed/new rows with `vault_path`, `note_type`, `scope`, `content_hash`, `metadata_json`, and optional `memory_id`.
5. Syncs only changed/new `type: minx-memory` notes.
6. Requires valid memory identity: `memory_key`, `scope` or legacy `domain`, and `memory_type`.
7. Validates payload through `validate_memory_payload`.
8. Creates vault-authored memories as active with `confidence=1.0`, `source='vault_sync'`, and `actor='vault_sync'`.
9. Auto-confirms a matching candidate memory, then applies payload replacement.
10. Updates a matching active memory payload.
11. Never mutates rejected or expired memories. It warns and skips.
12. Emits `memory_events` rows for successful lifecycle changes:
    - create path: `created`, `promoted`, `vault_synced`
    - candidate path: `confirmed`, `payload_updated`, `vault_synced`
    - active update path: `payload_updated`, `vault_synced`
13. Emits orphan `vault_synced` only when the orphaned index row references an active memory whose `source='vault_sync'`.
14. Deletes orphan `vault_index` rows only after a complete vault walk. "Complete" means path enumeration succeeded; per-file read failures warn and skip without blocking orphan cleanup. If path enumeration fails, orphan cleanup is skipped to avoid wiping the index from a transient filesystem error.
15. Runs in one write transaction; `dry_run=True` rolls back after computing the report.

### 5.4 MCP Surface

Core exposes:

```text
vault_scan(dry_run: bool = False) -> {report: VaultScanReport}
```

This is an admin/diagnostic tool. Default daily snapshots do not automatically scan the vault unless a future call site explicitly consumes `settings.vault_scan_on_snapshot`.

## 6) Slice 6d: Snapshot MemoryContext

### 6.1 Contract

`DailySnapshot.memory_context` contains compact DTOs, not raw service records:

```python
@dataclass(frozen=True)
class MemoryContextItem:
    id: int
    memory_type: str
    scope: str
    subject: str
    confidence: float
    payload: dict[str, object]
    source: str
    reason: str
    updated_at: str

@dataclass(frozen=True)
class MemoryEventItem:
    id: int
    memory_id: int
    event_type: str
    actor: str
    created_at: str
    payload: dict[str, object]

@dataclass(frozen=True)
class MemoryContext:
    active: list[MemoryContextItem]
    pending_candidate_count: int
    recent_events: list[MemoryEventItem]
```

### 6.2 Snapshot Behavior

- `active` includes active memories only; TTL-expired active rows are excluded. Ordering is deterministic: `updated_at DESC, id DESC`, bounded to 100.
- `pending_candidate_count` counts current candidate memories.
- `recent_events` contains the most recent memory events, ordered by `id DESC` and bounded to 50.
- If candidates exist, `attention_items` includes `N memory candidates need review.`
- Memory context is best-effort. Failures return a `PersistenceWarning` with `sink='memory_context'`.
- Detector memory proposal ingestion failures return a `PersistenceWarning` with `sink='memory_proposals'`.

### 6.3 Detector Visibility Decision

For 6d, `MemoryContext` is snapshot/harness context only. It is built after detectors run. Giving detectors memory context is a future behavior change and should get its own design note because it changes detector output semantics.

## 7) Slice 6e: Wiki Templates and Deterministic Section Updates

### 7.1 Templates

Templates ship as package data:

- `minx_mcp/core/templates/wiki/entity.md`
- `minx_mcp/core/templates/wiki/pattern.md`
- `minx_mcp/core/templates/wiki/review.md`
- `minx_mcp/core/templates/wiki/goal.md`

Template placeholders use Python `string.Template` syntax: `${name}`, `${date}`, `${scope}`, etc. Do not use `{{name}}`.

Templates must make the human edit path obvious. Each memory/wiki page template should separate:

- YAML frontmatter for deterministic machine sync.
- A short human-readable title.
- A `## Summary` section for Hermes-maintained prose.
- A `## Human Editable` section for user-authored notes.
- A `## System Metadata` or equivalent short note explaining that frontmatter is sync metadata.

The templates should avoid asking users to edit `payload_json`, `memory_key`, `memory_id`, or `sync_base_updated_at` during normal use. Those fields are visible for transparency and auditability, not because they are the preferred human interface.

This is a migration of the currently shipped wiki templates, not a parallel template family. Existing `entity`, `pattern`, `review`, and `goal` templates use `type: minx-wiki`; they should keep `type: minx-wiki` and gain the human-edit affordances above. The `type: minx-memory` frontmatter contract applies only to memory notes that reconcile into SQLite, not to general wiki pages.

### 7.2 Resource Surface

Core exposes:

- `wiki-templates://list` -> JSON list of template names.
- `wiki-templates://{name}` -> raw template text.

Unknown template names raise `InvalidInputError`.

### 7.3 Tool Surface

Core exposes:

```text
vault_replace_section(relative_path, heading, body) -> {path: absolute_path}
```

Semantics:

- Path is restricted to `Minx/`.
- `heading` maps to an exact `## {heading}` marker.
- Replacement ignores matching text inside fenced code blocks.
- The first matching level-2 section is replaced.
- If no section exists, a new `## {heading}` section is appended.
- Writes are atomic and guarded by the existing `VaultWriter` file lock.

Hermes decides what body text to write. Core only applies deterministic replacement.

### 7.4 Human Edit Boundary

6e makes wiki pages usable to read and safely edit, but it does not make Core a prose interpreter.

- Human edits in prose sections are preserved by deterministic section replacement unless Hermes intentionally updates that section.
- Core tools may replace named sections but must not rewrite unrelated prose.
- Frontmatter remains the only Core-owned sync surface for 6f.
- Hermes may later translate prose edits into structured memory proposals, but that must go through a visible confirmation path.

## 8) Slice 6f: Vault Reconciliation

6f is not a general markdown merge engine. It reconciles bounded edits in `minx-memory` frontmatter into SQLite.

It is intentionally separate from `vault_scan`. Scanning/indexing is low-risk; reconciliation mutates trusted memory and may also refresh note frontmatter.

### 8.1 Reconciliation API

Core exposes:

```text
vault_reconcile_memories(dry_run: bool = False) -> {report: VaultReconcileReport}
```

`VaultReconcileReport` is JSON-serializable:

```python
@dataclass(frozen=True)
class VaultReconcileWarning:
    kind: str
    vault_path: str
    message: str
    memory_id: int | None = None
    memory_key: str | None = None
    db_updated_at: str | None = None
    sync_base_updated_at: str | None = None

@dataclass(frozen=True)
class VaultReconcileReport:
    scanned: int
    applied: int
    created: int
    confirmed: int
    updated: int
    skipped: int
    conflicts: int
    warnings: list[VaultReconcileWarning]
```

Warning `kind` values are:

- `invalid_note`
- `identity_mismatch`
- `missing_memory`
- `conflict`
- `terminal_state`
- `walk_failed`
- `write_failed`

`dry_run=True` computes the same report and rolls back all SQLite writes and note-frontmatter writes.

### 8.2 Link Resolution

Resolution order:

1. If `memory_id` is present, load that row. It must match `memory_key`, `scope`, `memory_type`, and `subject`.
2. If `memory_id` is absent, resolve by `memory_key` to a live `(scope, memory_type, subject)` row.
3. If no live row exists, check the latest row for the same `(scope, memory_type, subject)`. "Latest" is defined as `ORDER BY updated_at DESC, id DESC LIMIT 1` — `id DESC` is the deterministic tie-breaker when two rows share an `updated_at` at the DB's second granularity.
4. If the latest row is rejected or expired, warn with `kind='terminal_state'` and skip. Do not recreate a fresh live row for a terminal memory just because the note has no `memory_id`.
5. If no row exists at all, create a new active memory only when the note has valid identity and valid payload.
6. If `memory_id` points to no row, warn with `kind='missing_memory'` and skip. Do not silently recreate a stale ID.
7. If `memory_id` exists but disagrees with `memory_key`, `scope`, `memory_type`, or `subject`, warn with `kind='identity_mismatch'` and skip. Do not guess which identity is correct.

Successful resolution repairs `vault_index.memory_id` for that note when needed.

### 8.3 Conflict Policy

For notes generated from SQLite, 6f requires `sync_base_updated_at`. The policy distinguishes **active-row edits** (destructive: overwriting a live memory payload) from **candidate confirmations** (additive: promoting a detector-proposed memory the user has clearly accepted by templating it into the vault).

Before applying a vault edit:

- **Active target, `sync_base_updated_at` present:** if `row.updated_at == sync_base_updated_at` as an exact string comparison, the edit may apply. If they differ, reconciliation must not overwrite SQLite. It reports a `kind='conflict'` warning containing `memory_id`, `memory_key`, `db_updated_at`, and `sync_base_updated_at`.
- **Active target, `sync_base_updated_at` absent on a note with `memory_id`:** reconciliation warns and skips. Hermes can ask the user whether to refresh the note or force an explicit tool update.
- **Candidate target:** detector-proposed candidates may be materialized into vault notes before they carry a `sync_base_updated_at`, so confirmation is allowed without the version field. If `sync_base_updated_at` is present, it must still match exactly; a mismatch is still a `kind='conflict'`. Candidate confirmations transition the row to active via the §8.5 state policy.
- **Vault-authored note (no `memory_id`), matches an existing live row with `sync_base_updated_at`:** use the exact-string conflict policy as for active targets.
- **Vault-authored note (no `memory_id`), matches an existing live row without `sync_base_updated_at`:** only apply automatically when the existing row has `source='vault_sync'`. If the existing row has any other source, warn with `kind='conflict'` and skip because the note has no version field proving it was based on the current row.
- **Vault-authored note (no `memory_id`), no matching row:** falls through to the `kind='terminal_state'` guard (§8.2) and then to the §8.5 creation path.

This avoids ambiguous file modification timestamps and uses the version field Core can verify.

### 8.4 Payload Compatibility

6f writes canonical `payload_json`, but it may read older 6c-era note shapes:

- Prefer `payload_json`.
- Accept `value_json` as a legacy alias when `payload_json` is absent.
- Accept non-reserved frontmatter keys as payload only for vault-authored notes that have no `memory_id`. This keeps early hand-authored notes usable without letting generated notes drift into implicit schemas.

After any successful apply, note refresh canonicalizes the frontmatter to `payload_json`.

### 8.5 State Policy

- Candidate row + valid vault note: confirm and update, then emit `confirmed`, `payload_updated`, and `vault_synced`.
- Active row + valid payload: update payload, then emit `payload_updated` and `vault_synced`.
- New valid vault-authored note without a matching row: create active memory, then emit `created`, `promoted`, and `vault_synced`.
- Rejected or expired row: skip and warn. Vault edits do not resurrect terminal states.
- Deleting a note does not expire a memory in 6f. Deletion intent requires an explicit tool call or a future confirmation flow.

Reconciliation may update active memories whose current `source` is not `vault_sync` only when identity matches and the conflict policy passes. The audit actor is still `vault_sync` because the mutation source is the vault.

### 8.6 Note Refresh After Successful Apply

After every successful apply, Core refreshes the note frontmatter deterministically without rewriting unrelated prose sections. The refreshed frontmatter must include:

- `type: minx-memory`
- canonical `scope`
- canonical `memory_key`
- canonical `memory_type`
- canonical `subject`
- canonical `memory_id`
- canonical `payload_json`
- new `sync_base_updated_at` equal to the post-update SQLite `memories.updated_at` string

This prevents the next reconciliation from falsely conflicting against the just-applied edit.

### 8.7 Per-Note Apply Order and Failure Isolation

Reconciliation processes notes independently.

- A vault walk failure returns a `walk_failed` warning and performs no orphan cleanup or reconciliation.
- Each note runs inside a savepoint.
- For each changed note, the non-dry-run order is:
  1. Parse identity and payload.
  2. Open a SQLite savepoint.
  3. Resolve identity and terminal/conflict policy.
  4. Apply the SQLite memory mutation and lifecycle events.
  5. Re-read the mutated memory row to capture the exact post-update `updated_at`.
  6. Render canonical refreshed frontmatter with that post-update `updated_at`.
  7. Write refreshed frontmatter through `VaultWriter` without changing unrelated prose.
  8. Release the savepoint and commit the note's DB changes.
- `dry_run=True` performs parsing, resolution, validation, and report counting but does not write note files. Any DB simulation runs under a savepoint that is rolled back before returning.
- Invalid notes warn and continue.
- Conflicts warn and continue.
- Terminal-state notes warn and continue.
- A note-frontmatter write failure rolls back that note's DB changes, appends a `kind='write_failed'` warning, increments `skipped`, does not increment `applied`, and continues to later notes.
- Unexpected exceptions roll back the current note before propagating unless the implementation can safely convert them into a report warning.

SQLite and filesystem writes cannot be made perfectly atomic together. The required ordering above keeps the common failure case safe: if the note write fails, the DB mutation is still inside the savepoint and rolls back. If the final DB commit fails after the note write succeeds, the next reconciliation will see a version mismatch and report a conflict rather than silently overwriting.

### 8.8 Audit

Every successful vault-driven SQLite mutation emits `memory_events(event_type='vault_synced', actor='vault_sync')` with:

```json
{
  "vault_path": "Minx/Memory/timezone.md",
  "content_hash": "...",
  "change": "create|update|confirm_and_update|orphaned"
}
```

Conflict and invalid-note warnings are report output, not memory events.

The `vault_synced` event is the cross-system audit marker. It does not replace lifecycle events such as `created`, `promoted`, `confirmed`, or `payload_updated`; those events are emitted alongside it when the lifecycle state or payload changes.

## 9) Test Matrix

6c must cover:

- Migration `018` applies to fresh and pre-6c DBs.
- `vault_index` rows insert, update, and fast-path unchanged scans.
- `vault_synced` is accepted by `memory_events`.
- Invalid memory notes warn, are indexed, and do not mutate `memories`.
- Walk failure does not trigger orphan cleanup.
- Orphan cleanup deletes stale `vault_index` rows and gates orphan events to active `vault_sync` memories.
- Scanner create/update/candidate-confirm/terminal-skip paths.
- `vault_scan` MCP tool returns a serialized report.

6d must cover:

- Empty memory context.
- Active memory DTOs vs pending candidate counts.
- Recent memory events.
- Candidate attention item.
- Memory proposal failure warning.
- Snapshot archive determinism on repeat builds.

6e must cover:

- Template resources list and fetch all shipped templates.
- Templates include human-readable structure with `## Summary`, `## Human Editable`, and sync-metadata guidance.
- Existing `type: minx-wiki` template frontmatter remains valid while sections migrate to the human-edit contract.
- Wheel package includes wiki templates.
- `vault_replace_section` replaces first exact section outside fenced code blocks.
- Missing section append behavior.
- Path traversal rejection.
- File-lock behavior is preserved.
- Section replacement preserves unrelated human prose.

6f must cover:

- `vault_reconcile_memories(dry_run?)` MCP tool returns a serialized `VaultReconcileReport`.
- Report warning shape and warning `kind` values.
- `memory_id` match and mismatch.
- Missing `memory_id` row warns and skips.
- `memory_key` fallback.
- Latest terminal row for `memory_key` warns and skips even when `memory_id` is absent.
- Exact-string `sync_base_updated_at` clean apply and conflict skip.
- Missing `sync_base_updated_at` on a generated note warns and skips.
- Existing non-`vault_sync` live row without `sync_base_updated_at` warns and skips.
- Legacy `value_json` and hand-authored non-reserved payload keys are accepted only under the compatibility rules, then canonicalized to `payload_json`.
- Candidate confirmation.
- Active update for non-`vault_sync` memories when identity and conflict policy pass.
- New vault-authored memory creation.
- Terminal-state skip.
- Payload validation failures.
- Per-note failure isolation: invalid/conflicted/terminal notes do not stop other notes.
- Note-frontmatter write failure rolls back that note's DB mutation.
- Successful apply refreshes frontmatter with canonical identity, payload, `memory_id`, and post-update `sync_base_updated_at`.
- `dry_run=True` rolls back DB and note-frontmatter changes.
- Audit event payload shape and lifecycle-event sequencing.

## 10) Readiness Gate for Next Phases

Proceed from 6c to 6d-6f only when:

- `uv run pytest -q` is green.
- `uv run mypy minx_mcp` is green.
- `uv run ruff check .` is green.
- Subagent/code review finds no blocker for scanner failure modes, memory identity, orphan events, or snapshot warning surfaces.
- `HANDOFF.md` records the actual shipped state and the remaining 6d-6f work.
