# Slice 6c Implementation Plan — Vault Scanner + Pre-Work Hardening

**Date:** 2026-04-17
**Depends on:** 6a (MemoryService, memories/memory_events tables), 6b (snapshot archives),
`VaultReader` (BOM + symlink-safe) and `VaultWriter` (flock-guarded) primitives already
landed.
**Spec:** `docs/superpowers/specs/2026-04-15-slice6-durable-memory.md` §4 (vault_index),
§5 (MCP tools), §8 (scanner behavior), §13 (testing strategy).

## 0) Goal

Stand up the vault → SQLite half of the three-tier memory design: a walker that parses
Minx-managed markdown notes, maintains a `vault_index` table, and — for
`type: minx-memory` notes — keeps the `memories` row aligned with the note's
frontmatter. 6c is the *read side* of vault sync; 6e/6f add the write/bidirectional
flows.

## 1) Pre-Work Hardening (Items 1–4)

Four correctness rails were landed on `main` prior to starting 6c to keep the scanner
honest. **Status: shipped + regression-tested.**

| # | Area | Fix | Regression test |
| - | ---- | --- | --------------- |
| 1 | `core/goal_capture_llm._capture_with_llm` | Stop swallowing `LLMError`; only log-and-regex-fallback generic runtime exceptions. Preserves the `LLM_ERROR` contract documented in HANDOFF. | `tests/test_goal_parse_llm_fallback.py::test_contract_llm_error_propagates_instead_of_regex_fallback` |
| 2 | `meals/recipes.parse_recipe_note` + `_split_frontmatter` | Decode with `utf-8-sig` (BOM-safe), hash raw on-disk bytes for `content_hash`, and use `splitlines()` so CRLF/LF/lone-CR files all detect `---` fences identically. Brings meals recipe parsing into parity with `VaultReader`. | `test_parse_recipe_note_handles_crlf_line_endings`, `test_parse_recipe_note_handles_utf8_bom` |
| 3 | `finance/analytics.sensitive_query_count` | Apply `amount_cents < 0` filter so count is symmetric with `sensitive_query_total_cents` and with `FinanceReadAPI.get_filtered_transaction_count`. Audit summary updated to `aggregate intent=count_spending_transactions`. | `test_analytics.py::test_sensitive_query_count_is_spending_only_and_matches_total` |
| 4 | `meals/service._reconcile_vault_recipes_inner` | Resolve DB-stored `vault_path` and verify `is_relative_to(root)` before probing the filesystem; escape paths orphan with `reason="vault_path_escapes_root"`. | `test_reconcile_vault_recipes_orphans_paths_that_escape_vault_root` |

All four are behind regression tests; `pytest -q` → 822 passing; `mypy minx_mcp` → 0
errors; `ruff check .` clean.

## 2) 6c Scope — Vault Scanner

### 2.1 Schema (migration `018_vault_index.sql`)

```sql
CREATE TABLE vault_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_path TEXT NOT NULL UNIQUE,         -- POSIX relative path, as emitted by VaultReader
    note_type TEXT,                          -- frontmatter 'type' (minx-memory | minx-entity | minx-pattern | minx-goal | minx-review | NULL)
    scope TEXT,                              -- frontmatter 'domain' field; named scope here to match memories.scope
    content_hash TEXT NOT NULL,              -- SHA-256 over on-disk bytes; matches VaultDocument.content_hash
    last_scanned_at TEXT NOT NULL DEFAULT (datetime('now')),
    metadata_json TEXT NOT NULL DEFAULT '{}', -- full frontmatter (post-scalar coercion) as JSON; body is NOT stored
    memory_id INTEGER REFERENCES memories(id) ON DELETE SET NULL -- back-reference for minx-memory notes
);

CREATE INDEX idx_vault_index_note_type ON vault_index(note_type);
CREATE INDEX idx_vault_index_memory_id ON vault_index(memory_id);
-- idx for scanner reconciliation (notes present in DB but missing on disk):
CREATE INDEX idx_vault_index_last_scanned_at ON vault_index(last_scanned_at);
```

Migration also **re-adds** `vault_synced` to `memory_events.event_type` CHECK (migration
016 deliberately dropped it for 6c to own). Use the same rebuild-and-copy pattern as
`016_memory_ttl_and_event_check.sql` but preserve all existing event rows (no filter).

### 2.2 New module `minx_mcp/core/vault_scanner.py`

```python
@dataclass(frozen=True)
class VaultScanReport:
    scanned: int                       # files walked this scan
    indexed: int                       # new vault_index rows inserted
    updated: int                       # rows whose content_hash changed
    unchanged: int                     # rows whose content_hash matched (fast path)
    orphaned: int                      # rows whose file no longer exists -> deleted from vault_index
    memory_syncs: int                  # minx-memory notes that touched the memories table
    warnings: list[str]                # per-file parse failures, non-fatal

class VaultScanner:
    def __init__(
        self,
        conn: sqlite3.Connection,
        vault_reader: VaultReader,
        memory_service: MemoryService,
        *,
        scope_prefix: str = "Minx",           # only walk this subtree
    ) -> None: ...
    def scan(self) -> VaultScanReport: ...
```

Behavior contract (locked by tests):

1. **Walk** — Use `VaultReader.iter_documents("Minx")` so symlink escape, BOM, and
   frontmatter quirks are centralized. Never bypass `VaultReader`.
2. **Fast path** — For each `VaultDocument`, look up `vault_index` by `vault_path`. If
   `content_hash` matches, update `last_scanned_at` only; increment `unchanged`.
3. **Upsert** — On hash mismatch (or first sight), upsert the row with the new
   `content_hash`, `metadata_json` (the frontmatter dict, JSON-encoded), `note_type`,
   `scope`. Increment `indexed` / `updated` accordingly.
4. **Memory sync** — If `note_type == "minx-memory"`:
   - Validate frontmatter contract: `memory_key` (required, format
     `{scope}.{memory_type_or_namespace}.{subject_slug}`), `domain` (required, must
     match `memory_key` scope prefix), `memory_type` (required, one of the four
     existing types from `memory_payloads`), `updated` (optional ISO date).
   - On invalid contract → append a `warnings` entry like
     `"{vault_path}: invalid minx-memory frontmatter: {reason}"`, skip the memory
     sync (but still index the row so future scans don't re-process it until
     `content_hash` changes). `note_type` stays `minx-memory`; scanner does not mutate
     frontmatter.
   - Compute `payload` from frontmatter minus the reserved keys (`type`, `domain`,
     `memory_key`, `memory_type`, `updated`). Run through
     `memory_payloads.validate_memory_payload` for the declared `memory_type`; skip
     with a warning on failure. Never touch `memories` on invalid payload.
   - Resolve the memory row by `(memory_type, scope, subject)`:
     - No live row → `MemoryService.create_memory(source="vault_sync")` at
       `confidence=1.0`, `status="active"` directly (vault-authored memories bypass
       the detector candidate → active gate — the user edited a note, intent is
       explicit). Set `vault_index.memory_id` to the new id.
     - Live `active` row → `MemoryService.update_payload(memory_id, payload,
       actor="vault_sync")` (no-op fast path if payload already equals the stored
       one).
     - Live `candidate` row → auto-confirm via `MemoryService.confirm_memory(...,
       actor="vault_sync")` then apply `update_payload`. Document rationale:
       a note existing with `type: minx-memory` is an explicit confirmation by the
       user.
     - Terminal row (`rejected`, `expired`) → warn + skip. Users must run
       `memory_expire` / re-create via tool; the scanner must not resurrect a
       terminated memory just because a stale vault file still exists. This matches
       the "terminal states are sticky by design" invariant from 6a hardening.
   - On any memory touch, emit `memory_events(event_type="vault_synced",
     actor="vault_sync")` with payload
     `{"vault_path": str, "content_hash": str, "change": "create|update|confirm_and_update"}`.
   - Increment `memory_syncs`.
5. **Reconciliation (orphan detection)** — After the walk, find rows in `vault_index`
   whose `last_scanned_at` is older than the scan start timestamp and delete them. If
   the deleted row has a `memory_id` referencing an `active` memory with
   `source="vault_sync"`, emit a `vault_synced` event with
   `{"change": "orphaned", "previous_vault_path": str}` but **do not** auto-expire
   the memory — that is a 6f concern (bidirectional sync has to decide whether the
   note was deleted intentionally or moved). Log a warning listing orphan memory_ids
   for harness visibility.
6. **Transaction shape** — Wrap the whole scan in a single
   `BEGIN IMMEDIATE` (or a top-level `SAVEPOINT` with explicit commit on success,
   matching the pattern from `meals/service.reconcile_vault_recipes`) so a failure
   mid-scan leaves `vault_index` consistent with pre-scan state. Per-file warnings
   are collected in `report.warnings` and do not abort the transaction.
7. **Logging** — Structured log per summary (`scanned`, `indexed`, `updated`,
   `unchanged`, `orphaned`, `memory_syncs`, `len(warnings)`). Warnings are logged at
   `WARNING` but truncated to 256 chars each to avoid leaking note body content.

### 2.3 MCP surface

No new MCP *tool* is required in 6c (per spec §5 table and HANDOFF "Planned MCP
Surface Additions"). The scanner is invoked either:

- **Opportunistically** from `build_daily_snapshot` (best-effort, wrapped like
  `_ingest_memory_proposals_best_effort`) so the daily snapshot sees memory updates
  produced by overnight vault edits; or
- **Explicitly** via an internal scheduler / harness-side cron (preferred to avoid
  extending snapshot latency — this is configurable via
  `settings.vault_scan_on_snapshot: bool = False` to start).

Add a thin admin tool `vault_scan(dry_run: bool = False)` to `core/server.py`
returning a serialized `VaultScanReport`. This is diagnostic-only; default harness
usage stays on the implicit path. Gate it behind the same sensitive-access audit log
Finance uses (scanner output can reveal note names).

### 2.4 Integration with `VaultReader`

`VaultReader` currently restricts walks to the `allowed_prefixes` tuple
(`("Minx",)` is the planned production default). The scanner *must* use
`VaultReader.iter_documents("Minx")` rather than reinventing the walk — this is
the only place that enforces symlink-escape safety.

If `vault_reader.iter_documents` discovers an unreadable file or a non-utf8 file,
the error propagates as `InvalidInputError`. Scanner catches per-file and appends
to `warnings`; a single bad file does not abort the scan.

## 3) Items 5–14 — Fold-In Mini-Tasks

Each item is scoped, testable, and landed alongside the scanner rather than as a
separate hardening wave so they don't drift. Ordered by urgency relative to 6c.

### (5) `MemoryService.ingest_proposals` partial-commit surface area
**Why for 6c.** 6c's `memory sync` loop iterates multiple notes and must report
per-note failures in `VaultScanReport.warnings`. The same visibility gap exists in
`snapshot._ingest_memory_proposals_best_effort` today — a mid-batch exception leaves
earlier proposals committed with no indication in `DailySnapshot.persistence_warning`.

**Fix.** Extend `MemoryService.ingest_proposals` to return a
`IngestProposalsReport` (successes, failures with reason strings). Callers
(`snapshot` and the new scanner) surface the failures in their own warning
channels instead of swallowing.

**Test.** New test forces a `ConflictError` mid-batch, asserts earlier proposals are
still committed AND `persistence_warning` / `VaultScanReport.warnings` enumerate the
failed (memory_type, scope, subject) triples.

### (6) `list_memories` TTL consistency
**Why for 6c.** Scanner will call `list_memories` to enumerate active memories for
the "compute-which-memories-to-sync" path. Current code only applies the
`expires_at > ?` guard when `status == "active"`; passing `status=None` returns
expired rows.

**Fix.** Move the TTL guard out of the status branch so `status=None` and
`status='active'` both filter expired rows. `status='expired'` listings keep
returning expired rows (that's the explicit listing of that tombstone state).

**Test.** `test_list_memories_omits_expired_rows_on_status_none`.

### (7) `core/events.emit_event` silent drop visibility
**Why for 6c.** Scanner emits `vault_synced` events on every memory touch; a silent
drop means "scanner ran, but the audit row never landed" — auditors can't
distinguish that from "scanner didn't run".

**Fix.** `emit_event` gains a `strict: bool = False` kwarg. When `strict=True`, it
re-raises instead of returning None on `ValidationError` / `IntegrityError` /
`DatabaseError`. Scanner passes `strict=True`; existing callers keep default
behavior. All drops are also logged at `ERROR` with a stable field set
(`event_type`, `domain`, `entity_ref`, `error_type`, `error_code`).

**Test.** `test_emit_event_strict_re_raises_validation_errors`,
`test_emit_event_logs_non_strict_drops_at_error`.

### (8) `finance/service.apply_category_rules` priority + kind handling
**Why for 6c.** Not directly scanner-touched, but it's a silent correctness bug on
the finance critical path that my audit subagent flagged. Rules stored with
`priority=5` are applied as `priority=0` so operator ordering is ignored; rules with
`match_kind != "merchant_contains"` are silently dropped.

**Fix.** Thread `priority` through `Rule` construction; widen the match-kind switch
to cover all documented kinds (audit rules.py for the full set) OR raise an
explicit warning log when an unhandled kind is encountered and skip — pick
whichever matches the current contract. Add a regression test per match-kind.

**Test.** `test_apply_category_rules_respects_priority_ordering`,
`test_apply_category_rules_warns_on_unhandled_match_kind`.

### (9) `preferences` robustness
**Why for 6c.** Scanner introduces no new `get_preference` callers, but (3)–(5) fold
in is cheap. Current `get_finance_anomaly_threshold_cents` does `int(<arbitrary JSON>)`
without catching `ValueError`; `get_preference` does not catch `JSONDecodeError`.

**Fix.** Wrap both in a `_coerce_preference_int(key, default)` helper that returns
the default + logs `WARNING` on malformed storage (don't leak the raw stored value in
the log — log only the key + error type). Add a contract doc-string note.

**Test.** `test_get_finance_anomaly_threshold_cents_returns_default_on_non_int`,
`test_get_preference_returns_default_on_malformed_json`.

### (10) `jobs._row_to_job` corrupt `result_json` robustness
**Why for 6c.** Scanner persists scan reports as jobs (future 6g integration). A
corrupt `result_json` today raises `JSONDecodeError`; the caller sees
`INTERNAL_ERROR` with no triage info.

**Fix.** `_row_to_job` catches `JSONDecodeError`, logs `ERROR` with `job_id` and
`error_type`, and returns the `Job` with `result={"error": "corrupt_result_json"}`
so callers can distinguish.

**Test.** `test_row_to_job_returns_error_payload_on_corrupt_result_json`.

### (11) `time_utils` IANA + naive-timestamp hardening
**Why for 6c.** `vault_index.last_scanned_at` and `memory_events.created_at` use
`datetime('now')` defaults (UTC-naive in SQLite). The scanner's per-scan reconcile
window uses `utc_now_isoformat()` to detect orphans — if any upstream code passes a
naive-local timestamp through `local_calendar_date_for_utc_timestamp`, a
snapshot-adjacent scan can misattribute the scan day, causing false-positive
orphan deletes at midnight.

**Fix.** `resolve_timezone_name` validates the IANA name against `ZoneInfo(name)`
at resolution time and raises `InvalidInputError` with a generic message if the
lookup fails. `local_calendar_date_for_utc_timestamp` asserts
`ts.tzinfo is not None`; callers that pass naive timestamps must mark them UTC
explicitly (`ts.replace(tzinfo=timezone.utc)`) — the helper does not silently
assume UTC anymore. Audit + fix the three to five callers that pass naive
timestamps today.

**Test.** `test_resolve_timezone_name_rejects_bogus_iana`,
`test_local_calendar_date_requires_aware_datetime`, plus callsite tests updated to
pass explicit UTC.

### (12) `launcher._terminate_all` hung-child handling
**Why for 6c.** Not scanner-specific; pure DevX hardening. A hung MCP child leaves
`scripts/start_hermes_stack.sh` in a bad state at shutdown, which has bitten us
twice during smoke runs.

**Fix.** Catch `subprocess.TimeoutExpired`, escalate to `proc.kill()`, then
`proc.wait(timeout=5)` again. Log each transition. Match the pattern from
`scripts/hermes_slice4_smoke.py`.

**Test.** `test_terminate_all_escalates_to_kill_on_timeout` using a `Popen` double.

### (13) `document_text` LiteParse stderr redaction at WARNING
**Why for 6c.** If the scanner ever ingests PDFs in 6e (wiki attachments), the
stderr-log path runs. Already capped to 512 chars at the `WARNING` level per
Wave-3 hardening, but the first 512 chars can still contain credit-card digits,
OAuth tokens, etc. that appear in PDFs.

**Fix.** Apply `JSONFormatter`'s secret-shape redaction to stderr previews before
logging. Same regex set (already centralized in `logging_config.py`).

**Test.** `test_litparse_stderr_redacts_secret_shapes_at_warning`.

### (14) `core/events.py` stale docstring + re-add `vault_synced`
**Why for 6c.** Migration 016 dropped `vault_synced` from the `memory_events`
CHECK, with a note that 6c would re-add it. The module docstring in
`core/events.py` still lists `vault_synced` as a valid event type — operators
deploying 6b ahead of 6c will trip the stale doc.

**Fix.** Migration `018_vault_index.sql` re-adds `vault_synced` to the CHECK
constraint (see §2.1). Update the docstring to list the full set and link to the
migration. `memory_payloads.py` (if it carries the allowed-event-type set) picks
up the new value.

**Test.** `test_memory_events_accept_vault_synced` (writes a row with the new type,
asserts no `IntegrityError`).

## 4) Migrations

| # | File | Purpose |
| - | ---- | ------- |
| 018 | `018_vault_index.sql` | Create `vault_index` + indexes; rebuild `memory_events` with `vault_synced` back in the CHECK |

Migration runner order matters — 018 depends on the rebuild pattern from 016. Keep
the single-source-of-truth rule (only `minx_mcp/schema/migrations/`).

## 5) Module / API Surface

New modules:

- `minx_mcp/core/vault_scanner.py` (≈250 LOC target) — `VaultScanner`,
  `VaultScanReport`, `IngestProposalsReport` helper types.

Touched modules:

- `minx_mcp/core/server.py` — register `vault_scan` admin tool; optional
  `build_daily_snapshot` hook gated by `settings.vault_scan_on_snapshot`.
- `minx_mcp/core/memory_service.py` — (5) return `IngestProposalsReport`; (6)
  factor TTL guard.
- `minx_mcp/core/events.py` — (7) `strict` kwarg + (14) docstring; (14)
  `vault_synced` re-add.
- `minx_mcp/core/memory_payloads.py` — unchanged in schema, but ingest accepts
  `source="vault_sync"` explicitly.
- `minx_mcp/time_utils.py` — (11).
- `minx_mcp/preferences.py` — (9).
- `minx_mcp/jobs.py` — (10).
- `minx_mcp/launcher.py` — (12).
- `minx_mcp/document_text.py` — (13).
- `minx_mcp/finance/service.py` + `minx_mcp/finance/rules.py` — (8).
- `minx_mcp/config.py` — add `vault_scan_on_snapshot: bool = False`.

## 6) Testing Strategy

**Unit** (per-module):
- `tests/test_vault_scanner.py` — fast path (hash match → unchanged), change
  detection (hash mismatch → update), new file indexing, orphan deletion, warning
  collection on malformed frontmatter, memory-sync create/update/confirm paths,
  terminal-state skip path, escape-root defense (regression leveraging the same
  `VaultReader` symlink/`..` guards).
- One test per fold-in (items 5–14) listed inline above.

**Integration**:
- End-to-end fixture vault in `tmp_path`: seed three `minx-memory` notes, one
  `minx-entity`, one orphan, one with a parse error. Run scanner. Assert
  `vault_index` rows, `memories` table state, `memory_events` rows, and
  `VaultScanReport` shape.
- Re-run with no file changes → everything hits the fast path
  (`unchanged == total`, `updated == 0`, `indexed == 0`, `memory_syncs == 0`).
- Mutate one note's frontmatter → single `updated` + single `memory_syncs` +
  single `vault_synced` event.
- Delete one note → single `orphaned`; memory row stays (per §2.2 #5).

**HTTP smoke** (`tests/test_hermes_http_smoke.py`):
- Add a `vault_scan` call after the memory-lifecycle block; verify the returned
  envelope shape and that a subsequent `memory_list` reflects any synced rows.

**Schema**:
- `tests/test_db.py::test_migration_018_applies_cleanly` — applies 018 on a
  pre-existing 6a/6b DB, asserts no CHECK violations on existing event rows.

**Determinism / safety**:
- Lock test: two processes call `vault_scan` concurrently (mock `iter_documents`
  to block briefly); second call should serialize cleanly on
  `BEGIN IMMEDIATE` — no partial-write interleaving.

## 7) Rollout & Operator Steps

- **Migration 018** runs automatically on next process start; it rebuilds
  `memory_events` so operators with a heavily-populated audit log should
  `PRAGMA integrity_check` post-migration as a safety net (document in HANDOFF
  "Post-Upgrade Operator Steps").
- **Default behavior** keeps `settings.vault_scan_on_snapshot = False`. Hermes
  cron runs an explicit `vault_scan` admin call out of band (to be coordinated
  with the Hermes handoff in §"Hermes Harness Readiness").
- **Rollback path** — dropping migration 018 is non-trivial because of the
  `memory_events` rebuild; the documented recovery is forward-only (land 6c+1
  that disables scanner writes). Gate the scanner feature flag
  (`vault_scan_on_snapshot` + admin-tool registration) behind the settings value
  so disabling is config-only.

## 8) Verification Checklist

Before marking 6c shipped:

- [ ] `pytest -q` green (baseline 822 → expected ~850+ after 6c tests)
- [ ] `mypy minx_mcp` → 0 errors (test-side count monotonic or decreasing)
- [ ] `ruff check .` clean
- [ ] Migration 018 applies to a production-shaped fixture DB without data loss
- [ ] New regression tests for items 5–14 all present and green
- [ ] HANDOFF updated: slice-status table row for 6c, operator step for
      migration 018 if applicable, "Planned MCP Surface Additions" trimmed
- [ ] Full `explore`/`code-reviewer` subagent review (see §9) confirms readiness
      for Slice 6d

## 9) 6d Readiness Gate

Slice 6d adds `MemoryContext` to `DailySnapshot` and wires it into
`build_read_models`. 6c lands the only schema + service changes 6d depends on:
the `vault_index` table, the `memory_id` back-reference, and the `vault_synced`
event type. Exit criteria before starting 6d:

- `MemoryService.list_memories(status="active")` reliably filters expired rows
  (item 6 above) — 6d's `MemoryContext` snapshots this list and must not
  surface tombstones.
- `ingest_proposals` returns a structured report (item 5) — 6d's
  `persistence_warning` for memory ingest failures depends on it.
- Scanner emits `vault_synced` events consistently (items 7, 14) — 6d's
  recent-memory-events slice of `MemoryContext` reads from this stream.
- No stale docs (item 14) — 6d code review will lean on `core/events.py` as
  the canonical reference.

When those rails are green + reviewed, 6d is a pure data-plumbing change with
no new write paths.

## 10) Out of Scope for 6c

Explicitly deferred (not blocking 6d):

- Bidirectional sync for deleted notes → 6f.
- Wiki templates at `minx_mcp/core/templates/wiki/` → 6e.
- `MemoryContext` on `DailySnapshot` → 6d.
- Semantic search + embeddings → 6g (only if 6c proves insufficient).
- Extending scanner to non-`Minx/` subtrees (Recipes, etc.) — Meals already
  owns its own recipe scanner; scope stays at `Minx/` in 6c.

## 11) Effort + Sequencing

Target: **2 working days** for the core scanner + fold-ins, matching the
spec's 1–2 day 6c estimate.

Recommended commit order (small, individually reviewable):

1. Migration 018 + `core/events.py` docstring / `vault_synced` re-add (item 14)
2. `time_utils` hardening (item 11) — unblocks scanner timestamps
3. `MemoryService` TTL + report (items 5, 6)
4. `core/events.emit_event` strict mode (item 7)
5. Preferences / jobs / launcher / document_text fold-ins (items 9, 10, 12, 13)
6. Finance rules bugfix (item 8) — independent of scanner, lands anywhere in
   the sequence
7. `VaultScanner` module + admin tool
8. HTTP smoke + integration tests
9. HANDOFF update + review gate

Each commit verified against `pytest -q` + `mypy minx_mcp` + `ruff check .` before
the next one lands.
