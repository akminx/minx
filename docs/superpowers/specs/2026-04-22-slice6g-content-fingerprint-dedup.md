# Slice 6g: Content Fingerprint Primitive + Memory Content Dedup

**Date:** 2026-04-22
**Status:** Implemented (2026-04-22) — v7 (sixth review iteration, shipped unchanged)
**Depends on:** Slice 6a–6f shipped: durable memory schema, lifecycle, ingest pipeline, vault reconciliation
**Related docs:**

- `docs/superpowers/specs/2026-04-15-slice6-durable-memory.md`
- `docs/superpowers/specs/2026-04-18-slice6cdef-vault-scanner-memory-context-wiki-sync.md`
- `docs/superpowers/specs/2026-04-19-slice9-agentic-investigations.md` (migration numbering coordination, §5 Schema (Core))
- `minx_mcp/finance/dedupe.py` (existing per-table fingerprint precedent)

## 0) What Changed In v7

v6 fixed the 11 items from the v5 review, but left two real issues: the MCP tool file path was cited as `minx_mcp/core/memory_tools.py` (a file that does not exist; the real file is `minx_mcp/core/tools/memory.py`), and the proposed MCP tool test assumed `first["data"]["id"]` but `memory_create` actually returns `{"memory": memory_record_as_dict(record)}` so the id is at `first["data"]["memory"]["id"]`. v7 fixes both and clarifies the `_insert_memory_and_events` local-variable rebinding at the `create_memory` caller:

### Critical fixes (1)

- **v6 → v7 MCP tool test id path corrected; file path corrected (C1 from v6 review).** v6 §6.2 proposed `first_id = int(first["data"]["id"])` for the MCP tool test. The actual success response from `memory_create` is `{"memory": memory_record_as_dict(record)}` (see `minx_mcp/core/tools/memory.py:170`), so the id lives at `first["data"]["memory"]["id"]`. v7 §6.2 updates the snippet to use the correct path. v7 also corrects every reference from `minx_mcp/core/memory_tools.py` (does not exist) to `minx_mcp/core/tools/memory.py` in §0, §6.2, and §14.

### Substantive fixes (1)

- **v6 → v7 `create_memory` caller uses existing local-variable names (S1 from v6 review).** v6 §7.1's `create_memory` pseudocode used `memory_type, scope, subject` — the names from the method's parameter list. The actual implementation at `memory_service.py:91–114` validates and rebinds to `mt, sc, sj` local variables before calling `_insert_memory_and_events`. An implementer copying the v6 snippet literally would get a `NameError`. v7 §7.1 adds a one-line "uses the validated locals `mt/sc/sj`/etc." note, and matches the existing convention in the snippet.

### History (cumulative)

v7 retains every v3, v4, v5, and v6 fix that survived review. The full review journey:

- v1 → v2: 3 critical issues (migration numbering, non-idempotent SQL, payload field mapping).
- v2 → v3: 4 critical issues (test backward-incompat, backfill SQLite-invalid rollback, self-contradiction, missing query change).
- v3 → v4: 15 issues (5 critical, 5 substantive, 5 smaller).
- v4 → v5: 14 issues (4 critical, 5 substantive, 5 smaller), including the load-bearing control-flow bug (fingerprint lookup was in the wrong branch).
- v5 → v6: 11 issues (4 critical, 4 substantive, 3 smaller), including the helper-signature bug, the missed MCP-tool test, the mis-stated merge transaction wrapper, and the backfill bucketing hazard.
- v6 → v7: 2 issues (1 critical, 1 substantive) — both documentation paths, no logic changes from v6.

### Superseded from v6 (archive)

v6's full fix list is retained for traceability:

### Critical fixes (4)

- **v5 → v6 `_memory_fingerprint_input` signature fixed to require `scope` / `subject` at all call sites (C1).** v5 §7.1 showed `fp = _fp_compute(*_memory_fingerprint_input(memory_type, payload))` but none of `PreferencePayload`, `PatternPayload`, `EntityFactPayload`, `ConstraintPayload` contain `scope` or `subject` — they are proposal/row attributes, not payload fields. v5 §7.3 added `scope_override` / `subject_override` kwargs to make `update_payload` work, but that's a patch on top of a broken base. v6 redefines the canonical signature as `_memory_fingerprint_input(memory_type: str, payload: dict[str, object], *, scope: str, subject: str) -> tuple[str, str, str, str, str]` — `scope` and `subject` are **required kwargs** at every caller (§5.2, §7.1, §7.2.2 step 4, §7.3, §8.4 backfill). The `scope_override` / `subject_override` names from v5 §7.3 are dropped; the new names are the canonical ones.
- **v5 → v6 second exact-equals `ConflictError.data` assertion caught (C2).** `tests/test_core_memory_tools.py:170–174` asserts `dup["data"] == {"memory_type": "preference", "scope": "core", "subject": "tz"}` on the MCP tool response. v5 §6.2 only called out `tests/test_memory_service.py:963–996`. v6 §6.2 now covers both tests, with a before-and-after block for each. The MCP tool serialization path (`minx_mcp/core/tools/memory.py`) passes `ConflictError.data` through verbatim, so the same 5-key shape appears on the wire.
- **v5 → v6 content-equivalence merge transaction boundary explicit (C3).** v5 §7.2.3 said "Start inside BEGIN IMMEDIATE (existing wrapper)" — but the existing wrapper at `memory_service.py:506` only opens on the live-prior merge branch. The load-bearing case (`row is None`, fingerprint matches a live row of a different triple) never entered that wrapper. v6 §7.2.3 explicitly opens `BEGIN IMMEDIATE` at the top of the content-equivalence merge block, with the same `try: ... except Exception: if self.conn.in_transaction: self.conn.rollback(); raise` pattern as `_insert_memory_and_events`. §7.2.2 step 6 now also documents the transaction-boundary invariant: content-equivalence merge owns its own transaction, independent of any other merge/insert site.
- **v5 → v6 backfill Pass 1 always recomputes from `payload_json` for collision bucketing (C4).** v5 §8.4 said "reuse stored `content_fingerprint` when non-NULL and not `--force`" for Pass 1. That opens a hazard: if a row's stored fingerprint is stale (manual edit, partial prior run, pre-fix bug), two live rows with the same logical content can land in different buckets, and Pass 1 would miss a real live-vs-live collision — subsequently Pass 2 would write both rows, violating the partial unique index with an `IntegrityError` and either failing the backfill or (if the second `UPDATE` succeeded and the first didn't) corrupting the dedup contract. v6 §8.4 **always** recomputes from `payload_json` for every live row during Pass 1's collision-bucketing; stored values are only consulted for write-skip in Pass 2 (where `computed == stored` means no UPDATE needed). The `--force` flag becomes: recompute always, write always (overwrite even non-NULL stored values with freshly-computed ones).

### Substantive fixes (4)

- **v5 → v6 §14 checklist no longer contradicts §7.1 on fingerprint ownership (S1).** v5 §14 item "computes the fingerprint, persists it" conflicts with v5 §7.1's "caller computes, helper persists". v6 §14 updates the item to match §7.1: "callers compute the fingerprint; `_insert_memory_and_events` receives it as the required `fingerprint` kwarg, persists it, and discriminates `IntegrityError` per §7.1 using `SELECT id` for both checks."
- **v5 → v6 "both triples have a live row at once" phrasing fixed (S2).** v5 §7.2.2 step 6 called this case "rare, operator-visible state that the partial unique index normally prevents from forming." The partial unique index on `content_fingerprint` for live rows categorically prevents it; the only way two live rows can share a fingerprint is if data pre-dates the index or has been corrupted. v6 §7.2.2 step 6 reframes this as an invariant: under normal operation this branch is unreachable for post-slice-6g rows; if reached, it indicates either pre-6g rows surfacing for the first time (unlikely since the backfill populates them) or index/DB corruption. The content-equivalence merge still handles the case safely.
- **v5 → v6 §12 rollback enumerates all signature changes (S3).** v5 §12 only mentioned reverting `update_payload`'s `scope_override`/`subject_override` kwargs. v6 §12 revises to: revert `_memory_fingerprint_input(memory_type, payload, *, scope, subject)` back to its pre-6g signature `(memory_type, payload)`, revert all call sites (`create_memory`, `ingest_proposals`, `update_payload`, backfill script), revert `_insert_memory_and_events`'s `fingerprint` kwarg, revert the `content_fingerprint = ?` column in the same-triple merge UPDATE.
- **v5 → v6 v3/v4/v5 review-count narrative cleaned (S4).** v5 §0 mixed review counts ("v4 fixed the 15 items from the v3 review, v5 fixes all 14 items"). v6 §0 explicitly states only v6's delta against v5; the full review history is maintained in the opening paragraph, not scattered across the fix bullets.

### Smaller fixes (3)

- **v5 → v6 §2 line anchor for `out2 == []` updated to line range (M1).** v5 cited `tests/test_memory_service.py:722` for the `assert out2 == []` line; the `out2 = ...` call spans line 721 and the assert is on line 722. v6 cites `721–722` to reduce drift risk.
- **v5 → v6 §4 / §9.1 normalization steps aligned (S7).** v5 §9.1 mentions `re.sub` while §4.1 lists the public contract (NFC, casefold, whitespace collapse, strip). v6 §4.1 adds an implementation note: "whitespace collapse uses `re.sub(r'\\s+', ' ', ...)`" so the two sections agree.
- **v5 → v6 §10.2 "content-equivalence merge takes precedence" test clarifies normalization story (S9).** v5's test creates subject=`"Netflix"` then proposes subject=`"netflix"`. v6 §10.2 confirms `_memory_fingerprint_input` casefolds (via `normalize_for_fingerprint`) so these map to the same fingerprint input — adds a one-line comment to the test step making this normalization dependency explicit.

## 1) Goal

Introduce a **shared content-fingerprint primitive** and apply it to the `memories` table as the first consumer. This establishes the canonical normalization + hashing contract that every subsequent dedup-capable surface (journal, investigation steps, vault notes, future embeddings rows) will reuse.

The concrete outcome for Slice 6g alone:

- New module `minx_mcp/core/fingerprint.py` with `normalize_for_fingerprint` and `content_fingerprint`.
- New column `memories.content_fingerprint TEXT` with a partial unique index on `(content_fingerprint)` for live rows, plus a full index for rejected-prior lookups.
- `MemoryService` computes the fingerprint on create/update, persists it, and translates fingerprint index violations into a typed `ConflictError` that names the existing memory.
- Ingest pipeline (`ingest_proposals`) consults the fingerprint to short-circuit content duplicates before attempting the structural-triple merge; both rejected-prior and fingerprint-rejected suppressions are surfaced via a new `suppressed` field on `IngestProposalsReport`.
- One-shot idempotent backfill script (`scripts/backfill_memory_fingerprints.py`) using a two-pass single-transaction algorithm.

Slice 6g does **not** touch vault_index, journal, investigations, or the content of payloads — only the memory row identity surface.

## 2) Boundary

Core owns the primitive and the memory-side consumer. Harness contract is unchanged: the `memory_create` / `memory_confirm` / `memory_reject` / `memory_expire` tool surface continues to return `MemoryRecord` DTOs and raise `INVALID_INPUT` / `CONFLICT` / `NOT_FOUND` as today.

The new `CONFLICT` sub-case (content fingerprint collision) is still a `ConflictError` envelope. Both conflict variants gain a `conflict_kind` discriminator in `data`, documented in §6. One existing test (§6.2) asserts `ConflictError.data` via exact-equals and gets updated in this PR.

One existing behavior changes (§7.2): proposals on structurally rejected subjects, which today are silently dropped, are now surfaced via `IngestProposalsReport.suppressed`. No existing test relies on the silent-drop side effect in a way that would fail after the change — verified by grep, with one nuance to call out explicitly: `tests/test_memory_service.py:721–722` asserts `out2 == []` on an `IngestProposalsReport` (with `out2` constructed on line 721 and the assert on line 722). The `IngestProposalsReport.__eq__(list)` override at `memory_service.py:54–57` compares only `succeeded`, so that assertion remains true after v6 regardless of `suppressed` contents. That behavior is preserved, not relied on; new assertions on `suppressed` contents live in §10.3. Snapshot emits an info-level log, not a warning. See §7.2 rationale.

## 3) Why a Shared Primitive, Not Another Per-Table Hash

The repo already has three separately-authored fingerprint implementations, each with slightly different normalization rules:


| Location                                                   | Normalization                                      | Hashed over                                                        |
| ---------------------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------ |
| `finance/dedupe.py::fingerprint_transaction`               | `casefold()` + `normalize_merchant`                | `account_id | posted_at | description | amount_cents | dedupe_key` |
| `core/vault_scanner.py::_sha256_file` (via `content_hash`) | none — raw bytes                                   | whole file body                                                    |
| `core/snapshot.py::_serialize_daily_snapshot_for_archive`  | `json.dumps(sort_keys=True, separators=(",",":"))` | canonical snapshot JSON                                            |


**Scope of the `finance/dedupe.py` citation:** this slice reuses only the fingerprint *computation* pattern (normalize + hash, one function per domain). It does not reuse the finance backfill's interrupt-safety pattern: finance catches `except Exception` only (`minx_mcp/finance/dedupe.py:83–86`), which leaks a dangling transaction on Ctrl-C. v5's backfill uses `except BaseException` (§8.1); a Slice 6m cleanup retrofits finance to match (§13).

None of the three existing implementations is wrong in its own scope. Left alone, Slice 6h–6l would add two to four more one-off implementations. Memory content dedup, vault frontmatter fingerprint, journal entry fingerprint, investigation step fingerprint — each would be free to pick its own Unicode folding, whitespace handling, and JSON canonicalization. That pattern is the actual bug this slice prevents.

A shared primitive constrains future slices to one normalization decision, one tested function, and one audit surface.

## 4) The Primitive

### 4.1 Module

`minx_mcp/core/fingerprint.py` — a leaf module with no MCP dependencies. Exports two functions and documents its normalization contract. Stdlib-only: `hashlib`, `re`, `unicodedata`.

### 4.2 Normalization Contract

```python
def normalize_for_fingerprint(text: str | None) -> str:
    """Canonical text form for content-based dedup.

    Deterministic transforms, applied in order:
    1. None or empty -> empty string.
    2. Unicode NFC (canonical composition). Collapses combining-mark
       variants (NFD "cafe" + combining acute vs NFC "café") into a
       single form.
    3. casefold() — Unicode-correct case folding. Not lower(). ß -> ss,
       İ -> i̇, etc.
    4. Whitespace collapse: any run of Unicode whitespace (spaces,
       tabs, newlines) -> single U+0020. Implementation note:
       re.sub(r"\s+", " ", text). The regex \s matches the Unicode
       whitespace class by default in Python 3.
    5. Strip leading and trailing whitespace.

    Does NOT:
    - Strip punctuation (`run 5k` != `run, 5k`; intent differs).
    - Remove diacritics beyond what NFC handles (`café` != `cafe`;
      accented spellings are meaningful in food/meals content).
    - Normalize numbers (`$5` != `5 dollars`; that is a semantic
      equivalence, not a lexical one).
    """
```

### 4.3 Hashing Contract — Exact Bytestring Formula

```python
def content_fingerprint(*parts: str | None) -> str:
    """SHA-256 of normalized parts joined with U+0000 (NUL).

    Exact formula:

        normalized = [normalize_for_fingerprint(p) for p in parts]
        payload = "\0".join(normalized)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    Consequences of this formula:
    - content_fingerprint() with no parts produces sha256(b"") =
      e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
      (the SHA-256 of the empty string, because "\0".join([]) is "").
    - content_fingerprint("") produces sha256(b"") — same digest as
      content_fingerprint(). A single empty part joined with nothing
      is still an empty string.
    - content_fingerprint("", "") produces sha256(b"\0") — different
      from content_fingerprint(""), because the separator appears.
    - Adding or removing parts always changes the digest beyond a
      one-empty-slot → zero-slots boundary (which is the only fixed
      point in the formula).
    - content_fingerprint("ab", "c") produces sha256(b"ab\0c") and
      content_fingerprint("a", "bc") produces sha256(b"a\0bc"); they
      differ because the NUL byte lands in different positions.

    Returns a lowercase hex digest (64 chars).
    """
```

The one-empty-part-vs-no-parts equivalence is acceptable because memory fingerprints always call the function with exactly 5 parts (see §5.2), so the equivalence never manifests in practice. The golden vector table in §10.1 pins this behavior to prevent a future refactor from adding an "append trailing separator" variant that would break digest stability.

### 4.4 Non-Goals

- Not a cryptographic identity. The fingerprint is an equivalence-class key for dedup, not a tamper-evidence primitive. Two rows sharing a fingerprint are considered "same content for dedup purposes" — no further claims.
- Not a similarity score. Near-duplicates (paraphrases, synonyms) are the embeddings slice's problem, not the fingerprint's.
- Not dependent on Python/SQLite version beyond stdlib `hashlib` + `unicodedata`.

## 5) Memory Schema Changes

### 5.1 Migration `020_memory_content_fingerprint.sql`

**Migration numbering coordination:** Slice 9's current spec reserves `020_investigations.sql` for the investigations table. Slice 6g is the next migration to ship, so it claims `020`. As part of this slice's PR, the Slice 9 spec (`docs/superpowers/specs/2026-04-19-slice9-agentic-investigations.md`) is updated to reference `021_investigations.sql`. Two edits, both in the `## 5) Schema (Core)` section's migration filename reference and in the phase-breakdown table (around lines 54 and 145 at current HEAD).

Why: `minx_mcp/db.py::_validate_migration_paths` requires contiguous numbering from `001`. Migration `020` cannot be skipped; the only choices are "6g takes `020`" or "6g waits for 9a". 9a has 4+ sub-steps ahead of it per HANDOFF.md, so 6g taking `020` is the efficient call.

**Idempotency model (repo convention):** Every `ALTER TABLE ... ADD COLUMN` in the migrations directory is raw (see `008_finance_phase2.sql`). SQLite provides no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, and `add_column_if_missing` in `minx_mcp/db.py` is a Python helper used by the migration runner itself for the `_migrations` table — it is not callable from a `.sql` file. Re-apply safety for user migrations comes from the runner-level checksum guard at `apply_migrations`, not from SQL-level conditionals. This migration follows the same convention.

The two indexes use `CREATE [UNIQUE] INDEX IF NOT EXISTS` for defense-in-depth against a hypothetical manual re-apply that bypasses the checksum guard.

```sql
-- Slice 6g: content-based deduplication for memories.
--
-- ADD COLUMN follows the repo convention (see 008_finance_phase2.sql): raw
-- ALTER TABLE, trusting the migration checksum guard in db.py for re-apply
-- safety. SQLite has no ADD COLUMN IF NOT EXISTS.

ALTER TABLE memories ADD COLUMN content_fingerprint TEXT;

-- Partial unique index: only LIVE rows compete for fingerprint exclusivity.
-- Rejected and expired rows retain their fingerprint for audit and for
-- the "re-propose after expiry" reopen path, but they do not block new
-- rows. Rationale mirrors the existing uq_memories_live_triple partial
-- index shipped in migration 015.
CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_content_fingerprint_live
    ON memories(content_fingerprint)
    WHERE content_fingerprint IS NOT NULL
      AND status IN ('candidate', 'active');

-- Full (non-partial) index on content_fingerprint. Required consumer:
-- ingest_proposals (§7.2) performs a "has this content been rejected
-- before?" lookup that must see rejected and expired rows, so a
-- partial-live-only index cannot serve it. Non-unique because rejected
-- rows can legitimately share a fingerprint with each other.
CREATE INDEX IF NOT EXISTS idx_memories_content_fingerprint_all
    ON memories(content_fingerprint);
```

No `CHECK` constraint is added — the column is nullable for pre-backfill rows, and the backfill script populates it in a single transaction before any new read path relies on it.

`**_split_sql_script` compatibility:** `db.py::_split_sql_script` splits on `sqlite3.complete_statement`. Each of the three statements above terminates with `;` on its own line. Migration 015 uses the same partial-index pattern successfully, so the splitter is known to handle `WHERE ... AND status IN (...)` correctly.

### 5.2 Fingerprint Input Contract

For `memories`, the fingerprint is computed over these parts, in this exact order:

```
memory_type, scope, subject, payload_note, payload_value_part
```

Where `payload_value_part` is **per-type** and must match `PAYLOAD_MODELS` in `minx_mcp/core/memory_payloads.py`:


| `memory_type` | Payload model       | `payload_value_part` source                              | Rationale                                                                                                                                            |
| ------------- | ------------------- | -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `preference`  | `PreferencePayload` | `payload.get("value")`                                   | The specific stated preference (e.g. `"always-on"` for a dark-mode preference).                                                                      |
| `pattern`     | `PatternPayload`    | `payload.get("signal")`                                  | The detector-named signal (e.g. `"weekly_meal_prep"`); distinguishes two patterns with the same subject but different detection signals.             |
| `entity_fact` | `EntityFactPayload` | `_canonical_aliases(payload.get("aliases"))` (see below) | Aliases are the identifying refinement for entity facts; normalized and sorted so Unicode form and list order do not fracture the equivalence class. |
| `constraint`  | `ConstraintPayload` | `payload.get("limit_value")`                             | The enforced limit value (e.g. `"100"` for a grocery budget).                                                                                        |


**Unknown memory type (fallback).** For types not listed above (future-registered or pre-schema), `_memory_fingerprint_input` returns the 5-tuple:

```
(memory_type, scope, subject, "", json.dumps(payload, sort_keys=True, ensure_ascii=False))
```

The `note` slot is empty (`""`); the whole payload goes into the `value_part` slot as canonical JSON. This layout is fixed — two implementers must produce identical digests for the same unknown-type payload. Known-type and unknown-type fingerprints cannot collide with each other on non-trivial content because the `value_part` shapes differ (one is a short field value, the other is a full JSON object literal starting with `{`); on trivial content (empty payload), both collapse to `(type, scope, subject, "", "")` or `(type, scope, subject, "", "{}")`, so an unknown-type row with an empty payload **does not** match a known-type row with an empty payload (`"" ≠ "{}"`).

`**_canonical_aliases` definition:**

```python
def _canonical_aliases(aliases: object) -> str:
    """Canonical JSON form of an aliases list for fingerprinting.

    Lives alongside _memory_fingerprint_input in memory_service.py.
    Normalize each alias first, then sort, so Unicode form drift cannot
    reorder the list between two rows with the "same" aliases.
    Non-string entries are stringified via str() — in practice
    coerce_prior_payload_to_schema would have dropped them, but belt-and-
    suspenders against arbitrary stored content.
    """
    if not aliases:
        return ""
    normalized = sorted(normalize_for_fingerprint(str(a)) for a in aliases)
    return json.dumps(normalized, ensure_ascii=False)
```

`normalize_for_fingerprint` applies to each alias individually before sorting, so two lists `["CAFÉ", "netflix"]` (NFC) and `["cafe\u0301", "netflix"]` (NFD) sort identically after normalization. The final `json.dumps` is then itself passed through `normalize_for_fingerprint` inside `content_fingerprint`, which is a no-op for already-normalized ASCII JSON brackets and NFC content.

Keys not in this list (`payload.category`, `payload.observed_count`, `payload.unit`, `payload.frequency`, `payload.kind`) are deliberately excluded. They are detector-provided refinements that should not fracture the equivalence class. Two memories with identical `memory_type/scope/subject/note/value_part` but differing `category` are considered duplicates and merged; migration 015's structural unique already blocks this case for live candidates with matching triples, and the content fingerprint widens that protection to survive subject casing/whitespace drift.

**Fingerprint is computed over the coerced payload, not the raw stored payload.** `ingest_proposals` already runs `coerce_prior_payload_to_schema` on stored rows before merging (`memory_service.py`). If the fingerprint used raw stored bytes, a pre-6a row carrying legacy junk (unknown keys, type mismatches) would never match a clean proposal's fingerprint, even when the coerced content is identical. Every fingerprint computation, including the backfill, must therefore:

1. If computing from a write-path payload: run `validate_memory_payload(memory_type, payload)` (payload is guaranteed valid).
2. If computing from a stored payload: run `coerce_prior_payload_to_schema(memory_type, payload_from_storage)`.
3. Compute the fingerprint over the result.

The helper signature is:

```python
def _memory_fingerprint_input(
    memory_type: str,
    payload: dict[str, object],
    *,
    scope: str,
    subject: str,
) -> tuple[str, str, str, str, str]:
    """Returns the 5-tuple (memory_type, scope, subject, note, value_part)."""
```

`scope` and `subject` are **required keyword arguments** because none of the Pydantic payload models (`PreferencePayload`, `PatternPayload`, `EntityFactPayload`, `ConstraintPayload` in `memory_payloads.py:14–45`) carry them — they are row/proposal attributes, not payload fields. Every caller (`create_memory`, `ingest_proposals`, `update_payload`, the backfill script) has `scope` and `subject` in hand and passes them explicitly. The field mapping is a memory-specific concern; the primitive in `fingerprint.py` stays generic and takes only positional string parts.

**Degraded dedup for corrupted stored rows (by design):** `coerce_prior_payload_to_schema` returns `{}` when `model.model_validate(filtered)` fails on the field-filtered dict (`memory_payloads.py:114–118`). For a row whose stored `payload_json` is so corrupt that no field survives coercion, the fingerprint degrades to `content_fingerprint(memory_type, scope, subject, "", "")` — effectively "same type/scope/subject and no recoverable payload". This is acceptable for this slice:

- Corruption at that severity is rare (Slice 6a–6f persisted via Pydantic write-paths, so the only path to `{}`-coerced rows is manual DB tampering or future-schema evolution that invalidates past data).
- A clean-vs-corrupt pair with the same triple generally will NOT fingerprint-match: the clean side fingerprints its actual `note`/`value_part`, the corrupt side fingerprints empty strings.
- **Edge case:** if the clean row's `note` and `value_part` are both legitimately empty (allowed by `PreferencePayload` and the other payload models, since all fields are `Optional`), its fingerprint equals the corrupt row's fingerprint. The two rows are then considered duplicates and the partial unique index blocks one of them — which is the correct outcome, because a row with no content payload is semantically equivalent to a row whose content did not survive coercion. No operator action needed in this edge case.
- Two corrupt rows with the same triple also fingerprint-match, which is semantically correct — they are equally unrecoverable for dedup purposes.
- Escape hatch: for any unwanted collision the operator resolves via `memory_reject` or `memory_expire`, which releases the fingerprint from the partial unique index.

If a future slice introduces a schema migration that invalidates significant volumes of stored payloads, that slice must re-populate `content_fingerprint` via its own migration (rule 6 in §11).

**Degraded dedup for unknown memory types (by design):** The unknown-type fallback's `value_part` is whole-payload JSON (shown above). This is less precise than the per-type mapping (detector-refinement keys like `category` participate in the fingerprint), so dedup is more brittle during the window between "a new type appears in a detector proposal" and "rule 7 of §11 lands a per-type mapping in the next PR". The fallback is safe-by-default: it will fingerprint-match identical duplicates and will fingerprint-differ when any key changes, including noise keys. v4 accepts this as the cost of having the system fail safe rather than crash on unknown types.

## 6) Error Contract

### 6.1 Evolved Conflict Shape (Both Variants)

Both conflict variants gain a `conflict_kind` discriminator and a `memory_id` pointer. Callers that did not branch on specific keys are unaffected at the envelope level (the code is still `error_code="CONFLICT"`); callers that want to discriminate get a single, consistent field to switch on.

**Pre-6g structural-triple conflict shape (emitted by the handler that raises `ConflictError` for live-triple collisions in `_insert_memory_and_events` today):**

```json
{
  "memory_type": "preference",
  "scope": "core",
  "subject": "Netflix"
}
```

**v4 structural-triple shape (Slice 6g emits):**

```json
{
  "conflict_kind": "structural_triple",
  "memory_id": 42,
  "memory_type": "preference",
  "scope": "core",
  "subject": "Netflix"
}
```

Added: `conflict_kind`, `memory_id`. Existing three keys unchanged.

**New content-fingerprint conflict shape (`_insert_memory_and_events` path):**

```json
{
  "conflict_kind": "content_fingerprint",
  "memory_id": 42,
  "memory_type": "preference",
  "scope": "core",
  "subject": "netflix",
  "existing_subject": "Netflix"
}
```

`memory_type/scope/subject` are the **proposed** values; `existing_subject` is the stored subject of the row the proposal collides with. This lets a client surface "you proposed `netflix`, but a live memory already exists as `Netflix`" without a second round-trip.

**New content-fingerprint-update conflict shape (`update_payload` path):**

```json
{
  "conflict_kind": "content_fingerprint_update",
  "memory_id": 42,
  "blocking_memory_id": 17
}
```

`memory_id` is the row being updated; `blocking_memory_id` is the live row whose fingerprint the new payload would collide with.

### 6.2 Test Compatibility — Concrete Replacement

The repository's existing `tests/test_memory_service.py:963–996` does:

```python
# Pre-6g (current), test_memory_service.py:963–996:
def test_create_memory_duplicate_live_triple_raises_conflict(tmp_path) -> None:
    """Migration 015's partial unique index surfaces as a CONFLICT error.

    Without explicit mapping, ``sqlite3.IntegrityError`` would surface as a
    generic INTERNAL_ERROR to MCP clients — obscuring an actionable operator
    mistake (trying to manually create a memory that already has a live row).
    """
    from minx_mcp.contracts import ConflictError

    svc = _fresh_memory_service(tmp_path)
    svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="tz",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="user",
        actor="user",
    )
    with pytest.raises(ConflictError) as excinfo:
        svc.create_memory(
            memory_type="preference",
            scope="core",
            subject="tz",
            confidence=0.4,
            payload={"category": "timezone", "value": "America/Los_Angeles"},
            source="user",
            actor="user",
        )
    assert excinfo.value.data == {
        "memory_type": "preference",
        "scope": "core",
        "subject": "tz",
    }
```

v5 updates it to:

```python
# Post-6g:
def test_create_memory_duplicate_live_triple_raises_conflict(tmp_path) -> None:
    from minx_mcp.contracts import ConflictError

    svc = _fresh_memory_service(tmp_path)
    created = svc.create_memory(                      # changed: capture the record
        memory_type="preference",
        scope="core",
        subject="tz",
        confidence=0.9,
        payload={"category": "timezone", "value": "UTC"},
        source="user",
        actor="user",
    )
    with pytest.raises(ConflictError) as excinfo:
        svc.create_memory(
            memory_type="preference",
            scope="core",
            subject="tz",
            confidence=0.4,
            payload={"category": "timezone", "value": "America/Los_Angeles"},
            source="user",
            actor="user",
        )
    assert excinfo.value.data == {                    # changed: 5-key dict
        "conflict_kind": "structural_triple",
        "memory_id": created.id,
        "memory_type": "preference",
        "scope": "core",
        "subject": "tz",
    }
```

Two changes, both in the same test function. The adjacent test at line 999 (`test_create_memory_conflict_detection_ignores_unrelated_integrity_errors`) asserts that unrelated `IntegrityError` cases (CHECK / NOT NULL / foreign-key / non-live-triple UNIQUE) propagate unchanged — that test does not assert on `ConflictError.data` at all and does not need editing. Lifecycle conflict assertions at lines 1103, 1171, 1213, 1245, 1297 check `{"memory_id", "expected_status"}` shapes and are also not affected.

**v6 / v7: second exact-equals assertion at `tests/test_core_memory_tools.py:142–174`.** The MCP tool test asserts the same three-key shape on the wire:

```python
# Pre-6g (current), tests/test_core_memory_tools.py:142–174:
def test_memory_create_duplicate_live_triple_returns_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_fn = get_tool(server, "memory_create").fn

    first = create_fn(
        "preference", "core", "tz", 0.9,
        {"category": "timezone", "value": "UTC"},
        "user", "",
    )
    assert first["success"] is True

    dup = create_fn(
        "preference", "core", "tz", 0.4,
        {"category": "timezone", "value": "America/Los_Angeles"},
        "user", "",
    )
    assert dup["success"] is False
    assert dup["error_code"] == "CONFLICT"
    assert dup["data"] == {
        "memory_type": "preference",
        "scope": "core",
        "subject": "tz",
    }
```

v7 updates the final assertion to match the expanded shape. The MCP tool success response for `memory_create` at `minx_mcp/core/tools/memory.py:170` returns `{"memory": memory_record_as_dict(record)}`, so the created row's id is at `first["data"]["memory"]["id"]` (not `first["data"]["id"]`). The MCP serialization path passes `ConflictError.data` through verbatim, so the new `conflict_kind` and `memory_id` keys appear on the wire without any tool-code change:

```python
# Post-6g:
def test_memory_create_duplicate_live_triple_returns_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_fn = get_tool(server, "memory_create").fn

    first = create_fn(                                  # changed: capture so memory_id is known
        "preference", "core", "tz", 0.9,
        {"category": "timezone", "value": "UTC"},
        "user", "",
    )
    assert first["success"] is True
    first_id = int(first["data"]["memory"]["id"])       # added (v7: correct path)

    dup = create_fn(
        "preference", "core", "tz", 0.4,
        {"category": "timezone", "value": "America/Los_Angeles"},
        "user", "",
    )
    assert dup["success"] is False
    assert dup["error_code"] == "CONFLICT"
    assert dup["data"] == {                             # changed: 5-key dict
        "conflict_kind": "structural_triple",
        "memory_id": first_id,
        "memory_type": "preference",
        "scope": "core",
        "subject": "tz",
    }
```

**Grep coverage claim:** there are exactly two exact-equals assertions on a 3-key `{"memory_type", "scope", "subject"}` conflict dict in the repository — the two above. A full search (`rg -n '"scope": "core".*"subject": "tz"' tests/`) plus a structural search for `data == \{` blocks in the test tree returns only these two sites. No other tests need editing for `ConflictError.data` shape changes.

### 6.3 Why Not Two Error Codes

Keeping both cases under `CONFLICT` respects the existing harness contract: a conflict is a conflict, and the resolution flow is the same. The discriminator inside `data` is informational, not a new tool contract.

### 6.4 Secret Handling

The `existing_subject` field is included in the error envelope. Because `subject` is a canonical memory identifier, it is treated as non-sensitive by every other tool that returns memory DTOs. If Slice 6h (secret scanner) later lands, write-path scanning ensures no secret material reaches the `subject` column in the first place, so the `existing_subject` echo is safe-by-default.

## 7) `MemoryService` Changes

### 7.1 Insert Path — `_insert_memory_and_events`

`_insert_memory_and_events` gains a new keyword argument `fingerprint: str` (required, no default — the caller is expected to always supply it). Callers:

- `ingest_proposals` passes the fingerprint computed in §7.2.2 step 4.
- `create_memory` computes it inline (same formula as `ingest_proposals`'s step 4).
- The backfill script does not go through this path (it writes direct SQL).

Inside the helper, the insert SQL gains a `content_fingerprint` column in the INSERT column list and the supplied `fingerprint` value in the VALUES list. Computing it again inside the helper (instead of passing it in) would risk drift if the two sites ever compute slightly differently (e.g. if `coerce_prior_payload_to_schema` behaves differently between them).

```python
from minx_mcp.core.fingerprint import content_fingerprint as _fp_compute

def _insert_memory_and_events(
    self,
    *,
    memory_type: str,
    scope: str,
    subject: str,
    confidence: float,
    status: str,
    payload: dict[str, object],
    source: str,
    reason: str,
    actor: str,
    emit_promoted: bool,
    fingerprint: str,                  # v6: required kwarg, caller-computed
) -> MemoryRecord:
    # ... existing INSERT SQL extended with content_fingerprint column,
    # which receives `fingerprint` as its bind value ...
```

And in `create_memory`, the caller computes the fingerprint first, passing `scope` and `subject` into `_memory_fingerprint_input` because they are row/proposal attributes, not payload fields.

**Note on variable names:** the existing `create_memory` body at `memory_service.py:91–114` validates and rebinds its parameters to local variables `mt`, `sc`, `sj`, `conf`, `body`, `src`, `reason`, `actor` (see the `_validate_`* calls at lines 104–110 and the payload validation at line 112). The pseudocode below uses those existing local names to avoid confusion for the implementer; no local-variable renames are introduced by this slice.

```python
# Existing locals from memory_service.py:91–114: mt, sc, sj, conf, body,
# src, reason, actor. v6/v7 adds the fingerprint computation below.
fp = _fp_compute(
    *_memory_fingerprint_input(mt, body, scope=sc, subject=sj)
)
return self._insert_memory_and_events(
    memory_type=mt,
    scope=sc,
    subject=sj,
    confidence=conf,
    status="candidate",
    payload=body,
    source=src,
    reason=reason,
    actor=actor,
    emit_promoted=False,
    fingerprint=fp,
)
```

The handler that raises `ConflictError` for live-triple collisions in `_insert_memory_and_events` is replaced with a two-step discrimination. The `SELECT 1` probe becomes `SELECT id` so the error envelope can carry the colliding row's id per §6.1. In the handler, the local name `fp` refers to the `fingerprint` kwarg.

```python
except sqlite3.IntegrityError as exc:
    if self.conn.in_transaction:
        self.conn.rollback()
    # Step 1: structural-triple live-row check (preserves pre-6g behavior
    # shape, now with memory_id + conflict_kind).
    live = self.conn.execute(
        """
        SELECT id FROM memories
        WHERE memory_type = ? AND scope = ? AND subject = ?
          AND status IN ('candidate', 'active')
        LIMIT 1
        """,
        (memory_type, scope, subject),
    ).fetchone()
    if live is not None:
        raise ConflictError(
            "A live memory already exists for this (memory_type, scope, subject)",
            data={
                "conflict_kind": "structural_triple",
                "memory_id": int(live["id"]),
                "memory_type": memory_type,
                "scope": scope,
                "subject": subject,
            },
        ) from exc
    # Step 2: content-fingerprint live-row check.
    fp_match = self.conn.execute(
        """
        SELECT id, subject FROM memories
        WHERE content_fingerprint = ?
          AND status IN ('candidate', 'active')
        LIMIT 1
        """,
        (fp,),
    ).fetchone()
    if fp_match is not None:
        raise ConflictError(
            "A live memory with equivalent content already exists",
            data={
                "conflict_kind": "content_fingerprint",
                "memory_id": int(fp_match["id"]),
                "memory_type": memory_type,
                "scope": scope,
                "subject": subject,
                "existing_subject": str(fp_match["subject"]),
            },
        ) from exc
    # Unknown violation — re-raise so it surfaces as the caller sees fit
    # (today this becomes INTERNAL_ERROR at the MCP boundary; no change).
    raise
```

Both self-verifying lookups use state, not SQLite error-message parsing; this matches the existing pattern in `_insert_memory_and_events` (the "don't trust error messages" comment block in the handler).

### 7.2 Ingest Path — `ingest_proposals`

#### 7.2.1 New Dataclass and Report Shape

`IngestProposalsReport` evolves to carry three parallel lists. `MemoryProposalSuppression` is new:

```python
@dataclass(frozen=True)
class MemoryProposalSuppression:
    memory_type: str
    scope: str
    subject: str
    reason: str  # "structural_rejected_prior" | "content_fingerprint_rejected_prior"


@dataclass(frozen=True)
class IngestProposalsReport:
    succeeded: list[MemoryRecord]
    failures: list[MemoryProposalFailure]           # invalid payloads, etc.
    suppressed: list[MemoryProposalSuppression]    # "not an error, but we skipped"
    # existing __iter__/__len__/__getitem__/__eq__(list) unchanged
```

`suppressed` is additive. The class's custom `__eq__` only compares to a `list` (using `succeeded`); `IngestProposalsReport(...) == IngestProposalsReport(...)` uses the dataclass default, which compares all three lists. No existing test constructs two `IngestProposalsReport` instances for comparison (verified by grep for `IngestProposalsReport(` outside `memory_service.py`), so adding `suppressed` is safe. A note in §11 records this for future consumers.

#### 7.2.2 Per-Proposal Control Flow

**Why ordering matters:** `ingest_proposals` today at `memory_service.py:447–576` forks the post-validation path into two branches:

- `if row is None or prior_status == "expired":` → `_insert_memory_and_events(...)` (line 487–502), which computes a fingerprint in §7.1 and could surface `ConflictError(conflict_kind="content_fingerprint")` if the proposal's content collides with another live row;
- else (live prior) → in-place merge (line 506–575).

If the fingerprint lookup ran **inside** either of those branches only, a proposal with a new `(memory_type, scope, subject)` but duplicate content (matching a live row of a different triple) would take the insert branch, hit the fingerprint partial unique index, and raise `ConflictError` rather than entering the content-equivalence merge. That defeats the main purpose of this slice — which is precisely to merge dupes across triples, not to raise on them. v5's control flow therefore runs the fingerprint lookup **before** the insert/merge fork, so both cases can route into the content-equivalence merge when applicable.

For each proposal, the ordering is:

1. **Structural lookup** (existing behavior, unchanged):
  ```sql
   SELECT * FROM memories
   WHERE memory_type = ? AND scope = ? AND subject = ?
   ORDER BY updated_at DESC, id DESC
   LIMIT 1
  ```
2. **If `prior_status == "rejected"`:** append `MemoryProposalSuppression(reason="structural_rejected_prior")` to `suppressed`, `continue`. (Existing code does a silent `continue`; v5 makes this observable via the new `suppressed` list. An info-level snapshot log per §2 and §7.2.4 surfaces the suppression without elevating to a warning.)
3. **Validate the proposal's payload** via `validate_memory_payload`. On failure: append to `failures`, `continue`. (Unchanged from today. Runs **after** the rejected check, preserving the existing "invalid payload on rejected subject is suppressed, not failed" contract.)
4. **Compute the fingerprint** from the validated payload via `_memory_fingerprint_input(memory_type, validated_payload, scope=proposal.scope, subject=proposal.subject)`. `scope` and `subject` come from the proposal; they are never read from the payload dict (the Pydantic models at `memory_payloads.py:14–45` do not carry them).
5. **Fingerprint lookup** (new step):
  ```sql
   SELECT id, status, memory_type, scope, subject, payload_json, confidence, reason
   FROM memories
   WHERE content_fingerprint = ?
   ORDER BY
     CASE status
       WHEN 'active' THEN 0
       WHEN 'candidate' THEN 1
       WHEN 'rejected' THEN 2
       WHEN 'expired' THEN 3
       ELSE 4
     END,
     id DESC
   LIMIT 1
  ```
   The `ORDER BY` prefers live rows over terminal rows for the same fingerprint, so the decision always reflects the current-live row when one exists. The `WHERE content_fingerprint = ?` uses `idx_memories_content_fingerprint_all`; the `ORDER BY CASE` applies to the small result set, not the index seek. The fetched `payload_json`/`confidence`/`reason` columns are consumed only on the content-equivalence merge branch of step 6; on all other branches they are read-cost overhead. At a single-row indexed lookup at single-user scale this cost is fixed-bounded and not worth branch-specific query variants.
6. **Dispatch on fingerprint-lookup result.** Let `fp_match` denote the row returned (or `None`) and `fp_match_same_triple` denote `fp_match is not None and (fp_match.memory_type, fp_match.scope, fp_match.subject) == (proposal.memory_type, proposal.scope, proposal.subject)`:
  - **No match (`fp_match is None`):** fall through to step 7 (existing insert/merge fork, unchanged).
  - **Top match is `rejected`:** append `MemoryProposalSuppression(reason="content_fingerprint_rejected_prior")` to `suppressed`, `continue`. No DB write, no event.
  - **Top match is `expired`:** fall through to step 7. The expired row retains its fingerprint, and the partial unique index permits this because expired rows are not in `('candidate','active')`. The expired row does not participate in merge; the existing `row is None or prior_status == "expired"` branch will insert a fresh `candidate`/`active` row (and its own fingerprint write won't collide on the partial unique index because the expired row's fingerprint is excluded by the partial predicate).
  - **Top match is `candidate`/`active` AND `fp_match_same_triple` is true:** fall through to step 7. The structural lookup and the fingerprint lookup agree on the same row; the existing in-place merge at `memory_service.py:506–575` applies naturally. Step 7 **must** detect this case (matched `fp_match.id == row.id`) and reuse the existing merge path without a second merge pass — a safety invariant enforced by a dedicated test (§10.2 overlap test).
  - **Top match is `candidate`/`active` AND `fp_match_same_triple` is false:** execute the **content-equivalence merge** per §7.2.3. This overrides both the insert branch (when `row is None` or `prior_status == "expired"`) and the merge branch (when the proposal has a live prior of its own triple — but the fingerprint lookup found a **different** live row ranked first by the `CASE` ORDER BY). In the latter "both triples have a live row at once, and they fingerprint-equal" case, v6 updates the fingerprint-matched row and leaves the proposal's own-triple prior unchanged.
    **Invariant note (v6):** under normal post-slice-6g operation, two live rows cannot share a fingerprint — the partial unique index `idx_memories_content_fingerprint_live` on `(content_fingerprint) WHERE status IN ('candidate','active')` categorically forbids it. The only way to reach this case is if pre-6g rows surface with matching coerced content the backfill missed (unlikely but possible if a row was created between backfill read and backfill commit — the single-transaction backfill design in §8 makes this window zero in practice) or if the database has been corrupted or manually edited. The content-equivalence merge still handles the case safely; an operator-facing log entry flags the unusual state for investigation.
7. **Existing insert/merge fork** from `memory_service.py:487–575` (bodies largely preserved, with two small v5 changes):
  - If `row is None or prior_status == "expired"`: existing `_insert_memory_and_events(...)` call at line 489 — now with fingerprint passed through (§7.1 handles persistence and collision translation). **v5 change: pass the step-4 fingerprint as a new keyword argument to `_insert_memory_and_events` rather than recomputing it inside.** This ensures step 5's lookup and step 7's insert agree on the same digest; recomputing would be cheap-but-duplicative and risks a skew if the two sites ever drift in their coercion behavior.
  - Else (live prior exists, same triple as the proposal): existing in-place merge loop (line 506–575). **v5 change: after computing `merged` and before the skip-write short-circuit, recompute the fingerprint over `merged` and include `content_fingerprint = ?` in the UPDATE** alongside the existing columns. Rationale: the merged payload can differ from both the stored payload and the new proposal's payload (shallow-merge overrides on shared keys), so its fingerprint can differ from the stored fingerprint. Failing to recompute would leave the `content_fingerprint` column stale on a merged row, breaking future fingerprint lookups. If the merge produces a fingerprint that collides with another live row (rare, would require a write path other than this one to have created the colliding row), the UPDATE hits the partial unique index and the existing `except Exception: rollback; raise` propagates — v5 does not wrap this in an `IntegrityError` handler because this case is an invariant violation (means migration 015's structural unique and the new fingerprint unique disagree), not a user-addressable conflict.

#### 7.2.3 Content-Equivalence Merge — Exact Semantics

This branch runs when step 6's fingerprint lookup finds a live row whose triple **differs from the proposal's** (i.e. `fp_match_same_triple` is false and `fp_match.status IN ('candidate', 'active')`). The existing merge mechanics are inherited **verbatim** from the merge loop at `memory_service.py:506–575`, with only two deltas:

1. The UPDATE targets the row whose id was returned by the fingerprint lookup (not the proposal's triple).
2. The `payload_updated` event's payload body is augmented with `merge_trigger` and `prior_identity`.

**Transaction boundary (v6 fix).** The existing same-triple merge at `memory_service.py:506` opens `BEGIN IMMEDIATE` only inside its own branch. The content-equivalence merge runs in a different branch (§7.2.2 step 6's "Top match differs from proposal's triple" dispatch), which does **not** pass through that wrapper. The content-equivalence merge therefore opens its **own** `BEGIN IMMEDIATE` at the top of the block, mirroring the pattern used by `_insert_memory_and_events` and the same-triple merge.

Full semantics:

```python
# v6: Open BEGIN IMMEDIATE for this branch — the content-equivalence
# merge runs outside both the same-triple merge wrapper and the
# _insert_memory_and_events wrapper.
self.conn.execute("BEGIN IMMEDIATE")
try:
    memory_id = int(fp_match["id"])
    prior_payload = _parse_payload_json(str(fp_match["payload_json"]))
    prior_payload_clean = coerce_prior_payload_to_schema(
        proposal.memory_type, prior_payload
    )
    merged = {**prior_payload_clean, **validated_payload}

    # Existing merge math, unchanged:
    new_confidence = max(float(fp_match["confidence"]), float(proposal.confidence))
    prior_status = str(fp_match["status"])
    new_status = prior_status
    promoted = False
    if new_confidence >= 0.8 and prior_status == "candidate":
        new_status = "active"
        promoted = True

    # Existing skip-write short-circuit. NOTE: the canonical JSON forms are
    # compared BEFORE writing, so a merge whose payload genuinely didn't
    # change produces no UPDATE, no event, and the returned record is the
    # existing row unchanged.
    payload_json = json.dumps(merged, sort_keys=True)
    stored_payload_json = json.dumps(prior_payload, sort_keys=True)
    if (
        payload_json == stored_payload_json
        and new_confidence == float(fp_match["confidence"])
        and new_status == prior_status
        and proposal.reason == str(fp_match["reason"])
    ):
        self.conn.commit()
        out.append(self.get_memory(memory_id))
        continue

    # The content_fingerprint column is NOT recomputed in the UPDATE. The
    # merged payload, by definition of reaching this branch, fingerprints
    # identically to the stored row (that's why the lookup matched). Keeping
    # the existing fingerprint in place is the correctness invariant.
    expected_status = prior_status
    cur = self.conn.execute(
        """
        UPDATE memories
        SET confidence = ?,
            payload_json = ?,
            reason = ?,
            status = ?,
            updated_at = datetime('now')
        WHERE id = ? AND status = ?
        """,
        (
            new_confidence,
            payload_json,
            proposal.reason,
            new_status,
            memory_id,
            expected_status,
        ),
    )
    if cur.rowcount != 1:
        if self.conn.in_transaction:
            self.conn.rollback()
        _raise_memory_status_conflict(memory_id, expected_status)

    # Delta #2: event payload body carries the merge-trigger discriminator
    # and the proposal's identity, so a future investigator can answer "why
    # did this row get an update when the detector fired on a different
    # subject?"
    _insert_event(
        self.conn,
        memory_id,
        "payload_updated",
        {
            "payload": merged,
            "prior_confidence": float(fp_match["confidence"]),
            "merge_trigger": "content_fingerprint",
            "prior_identity": {
                "memory_type": proposal.memory_type,
                "scope": proposal.scope,
                "subject": proposal.subject,
            },
        },
        actor,
    )

    # Existing promoted event emission, unchanged.
    if promoted:
        _insert_event(self.conn, memory_id, "promoted", {}, actor)

    self.conn.commit()
except Exception:
    if self.conn.in_transaction:
        self.conn.rollback()
    raise
out.append(self.get_memory(memory_id))
continue
```

**Contract with existing `payload_updated` consumers:** the event's `payload` field gains two new keys (`merge_trigger`, `prior_identity`) that do not appear on standard merge events. Consumers that read the event payload by key-lookup are unaffected; consumers that assert an exact-equals on the event payload dict must be updated. A grep over the `tests/` tree for `"payload_updated"` finds only partial-key assertions (`event["event_type"] == "payload_updated"`, `event["payload"]["payload"] == ...`); no exact-equals asserted on the full event payload dict. Other `tests/` consumers (`vault_reconciler`, `vault_scanner`) emit their own `payload_updated` events on unrelated entities and are not affected by this memory-service branch.

#### 7.2.4 Operator-Facing Message

`minx_mcp/core/snapshot.py` currently emits a `PersistenceWarning` labeled "skipped invalid proposals" whenever `report.failures` is non-empty. That branch is unchanged. A second branch is added for suppressions:

```python
if report.suppressed:
    suppressed_desc = ", ".join(
        f"{s.memory_type}:{s.scope}:{s.subject}" for s in report.suppressed[:5]
    )
    suffix = "" if len(report.suppressed) <= 5 else f" (+{len(report.suppressed) - 5} more)"
    # info-level log; not a PersistenceWarning because suppressions are
    # the "don't pester the user" contract working as intended, not a
    # snapshot degradation.
    logger.info(
        "Memory proposal suppressed (prior rejection): %s%s",
        suppressed_desc,
        suffix,
    )
```

Rationale: suppressions are expected behavior, not warnings. Failures (bad payloads, DB errors) remain warnings. `MemoryProposalSuppression.reason` is not surfaced in the log message itself (both structural and content-fingerprint suppressions log the same way) — the distinction is available to programmatic callers who inspect `report.suppressed` directly.

### 7.3 Update Path — `update_payload`

`update_payload` at `memory_service.py:302–346` today has no `IntegrityError` handler at all: it uses `except Exception: rollback; raise`, which causes an `IntegrityError` from the new fingerprint partial unique index to surface as a generic internal error. v5 adds a handler that probes for the blocking row and raises the typed `ConflictError`. Full pseudocode mirroring §7.1 shape:

```python
def update_payload(
    self,
    memory_id: int,
    *,
    payload: dict[str, object],
    actor: str = "system",
) -> MemoryRecord:
    _validate_actor(actor)
    row = self.conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Memory {memory_id} not found")
    expected_status = str(row["status"])
    if expected_status in {"rejected", "expired"}:
        raise InvalidInputError("Cannot update payload for rejected or expired memories")
    memory_type = str(row["memory_type"])
    payload = validate_memory_payload(memory_type, payload)
    payload_json = json.dumps(payload, sort_keys=True)
    # v6: recompute the fingerprint over the new payload. The row's
    # (memory_type, scope, subject) do not change on update_payload, so
    # they pass through from the existing row as required kwargs to
    # _memory_fingerprint_input.
    fp = content_fingerprint(
        *_memory_fingerprint_input(
            memory_type, payload,
            scope=str(row["scope"]),
            subject=str(row["subject"]),
        )
    )
    self.conn.execute("BEGIN IMMEDIATE")
    try:
        cur = self.conn.execute(
            """
            UPDATE memories
            SET payload_json = ?,
                content_fingerprint = ?,
                updated_at = datetime('now')
            WHERE id = ? AND status = ?
            """,
            (payload_json, fp, memory_id, expected_status),
        )
        if cur.rowcount != 1:
            if self.conn.in_transaction:
                self.conn.rollback()
            _raise_memory_status_conflict(memory_id, expected_status)
        _insert_event(
            self.conn, memory_id, "payload_updated",
            {"payload": payload}, actor,
        )
        self.conn.commit()
    except sqlite3.IntegrityError as exc:
        if self.conn.in_transaction:
            self.conn.rollback()
        # State-based probe (same pattern as §7.1): find the live row that
        # already holds this fingerprint. If we find one other than the row
        # we're updating, the IntegrityError is the content-fingerprint
        # partial unique. If we find nothing (or only ourselves — which
        # shouldn't be possible since our UPDATE was rolled back), the
        # IntegrityError came from somewhere unexpected; re-raise so it
        # surfaces as INTERNAL_ERROR at the MCP boundary.
        blocking = self.conn.execute(
            """
            SELECT id FROM memories
            WHERE content_fingerprint = ?
              AND status IN ('candidate', 'active')
              AND id != ?
            LIMIT 1
            """,
            (fp, memory_id),
        ).fetchone()
        if blocking is not None:
            raise ConflictError(
                "Updating this memory's payload would duplicate another live memory's content",
                data={
                    "conflict_kind": "content_fingerprint_update",
                    "memory_id": memory_id,
                    "blocking_memory_id": int(blocking["id"]),
                },
            ) from exc
        raise
    except Exception:
        if self.conn.in_transaction:
            self.conn.rollback()
        raise
    return self.get_memory(memory_id)
```

Two structural notes:

- `**_memory_fingerprint_input` signature (v6 canonical form).** The helper signature is `_memory_fingerprint_input(memory_type, payload, *, scope, subject)`; `scope` and `subject` are required kwargs (see §5.2). `update_payload` passes `scope=str(row["scope"])` and `subject=str(row["subject"])` because update operations do not change the row's triple — only its payload.
- **Race between blocking probe and UPDATE rollback.** The probe runs after the rollback. In principle another writer could have rejected or expired the blocking row in between, releasing the fingerprint from the partial unique index. In that case the probe returns no blocking row, v5 re-raises as `INTERNAL_ERROR`, and the caller retries — a sound outcome because the caller can now succeed. The alternative (raising `ConflictError` even when the probe returns nothing) would mislead the caller with a stale conflict; the alternative (looping to retry inside `update_payload`) would introduce unbounded retry and is out of scope. The current behavior matches the pattern set by `_insert_memory_and_events`.

### 7.4 Reject / Expire Paths

`reject_memory` and `expire_memory` do not touch the fingerprint. The partial index predicate (`status IN ('candidate','active')`) naturally releases the fingerprint when a row leaves the live set, so a new memory with the same content can be created after a rejection's 30-day TTL elapses or immediately after an expiration.

## 8) Backfill

### 8.1 Script Shape and Interrupt Safety

`scripts/backfill_memory_fingerprints.py`:

- Accepts optional DB path argument; defaults to `settings.db_path`.
- Idempotent: rows with non-null `content_fingerprint` are skipped unless `--force` is passed.
- Single-transaction writer lock: one `BEGIN IMMEDIATE` for the full two-pass algorithm (see §8.3 and §8.4).
- Uses `hashlib` + the new `fingerprint.py` primitive and the `_memory_fingerprint_input` helper — no ad-hoc hashing, no ad-hoc field selection.
- Loads each row's payload, runs `coerce_prior_payload_to_schema` (§5.2 invariant), then computes the fingerprint.
- Logs one info line per 500 rows processed, plus a final summary (rows scanned, rows written, rows skipped, collisions, elapsed seconds). Chunking affects only progress logging, not transaction boundaries.
- Exits with code 0 on clean run, 2 if any collisions are recorded.

**Interrupt safety — exact pattern:** the script wraps the full pass in `try`/`except BaseException`, rolling back on any termination (including `KeyboardInterrupt` from Ctrl-C) before propagating:

```python
conn.execute("BEGIN IMMEDIATE")
try:
    # full two-pass algorithm (§8.4) goes here
    conn.commit()
except BaseException:
    if conn.in_transaction:
        conn.rollback()
    raise
```

This ensures that Ctrl-C during a long backfill does not leave a dangling transaction holding the SQLite writer lock. The finance dedupe script currently catches only `Exception`; a Slice 6m cleanup pass retrofits finance to match (tracked in §13).

**SIGKILL / `os._exit`:** these cannot run Python cleanup. In that case the SQLite connection is torn down without a `COMMIT`; WAL replay on the next connection open rolls back the uncommitted transaction. Re-running the backfill is still safe. This is SQLite's default behavior, not something the script can or needs to handle explicitly.

### 8.2 Operator Step

HANDOFF.md gains a new "Post-Upgrade Operator Steps" entry:

> After pulling Slice 6g, run `python -m scripts.backfill_memory_fingerprints` once. The memory table gains a `content_fingerprint` column; rows that pre-date the migration start with NULL fingerprints and are excluded from fingerprint dedup until the backfill populates them. The backfill is idempotent and wrapped in a single writer-lock transaction; **do not call any MCP tool that writes to `memories` (including the daily snapshot) while the backfill is running.** For a single-user deployment, the simplest routine is: stop the Hermes stack, run the backfill (expect a few seconds at the ~10k-row scale used in the §8.4 memory budget; the plan doc measures actual scale before merge), restart. Re-running is safe, including after Ctrl-C or a crashed run.

### 8.3 Concurrent-Writer Correctness — Why a Full Writer Lock

A chunked-commit backfill would let another writer (Hermes daily snapshot detector, manual `memory_create`, etc.) land a row between chunks, whose fingerprint could then collide with a later backfill chunk and silently be masked.

v4 holds a single `BEGIN IMMEDIATE` writer lock for the full backfill pass. For a single-user local-SQLite deployment (WAL mode, per `db.py::get_connection`), this is the correct trade-off:

- SQLite WAL writer lock blocks other writers but not readers.
- The backfill is CPU-bound (`unicodedata.normalize` + `casefold` + SHA-256) at tens of microseconds per row; at realistic single-user scale a full-pass backfill completes in seconds.
- The operator step in §8.2 makes the expectation explicit: stop the Hermes stack during the backfill.
- If the backfill crashes or the operator Ctrl-Cs, the `except BaseException` at §8.1 rolls back every write, leaving the DB identical to its pre-backfill state. Re-running is safe.

### 8.4 Collision Handling — Two-Pass Algorithm

The backfill computes fingerprints for existing rows. A collision means two rows already violate the content-dedup invariant — which was never enforced before, so it is not a bug in the backfill; it is data that needs human resolution.

v4's two-pass algorithm never needs sub-transaction rollback. Critically, it separates **rows to consider for collision classification** (all live rows, always) from **rows to write this run** (only rows matching the idempotency filter):

**Pass 1 (read, no writes):**

1. Open a single `BEGIN IMMEDIATE` transaction (held for both passes).
2. Read **all** live rows (`status IN ('candidate', 'active')`) ordered by `(id ASC)`:
  ```sql
   SELECT id, memory_type, scope, subject, status, payload_json, content_fingerprint
   FROM memories
   WHERE status IN ('candidate', 'active')
   ORDER BY id ASC
  ```
   This includes rows that already have a non-NULL `content_fingerprint` (from a prior partial backfill or from the regular write path now emitting fingerprints). They must participate in collision bucketing so a re-run does not miss a collision between a previously-fingerprinted row and a not-yet-fingerprinted row.
3. Read **all** terminal rows (rejected/expired) for completeness of fingerprint assignment:
  ```sql
   SELECT id, memory_type, scope, subject, status, payload_json, content_fingerprint
   FROM memories
   WHERE status IN ('rejected', 'expired')
   ORDER BY id ASC
  ```
   (Terminal rows do not compete on the partial unique index, so they don't cause collisions among each other or with live rows, but they do need fingerprints assigned for §7.2's rejected-prior lookup to work.)
4. For each row in both result sets:
  - Load and coerce its `payload_json` per §5.2.
  - **Always compute the fingerprint from `payload_json`** (via `_memory_fingerprint_input(memory_type, coerced_payload, scope=row["scope"], subject=row["subject"])`). The stored `row.content_fingerprint` is **never** used for the bucketing step — doing so would create a correctness hazard where a stale/corrupt stored fingerprint (from a manual edit, partial prior run, or pre-fix bug) could route rows with the same logical content into different buckets. Pass 1's only job is to find real collisions; that requires a ground-truth hash.
  - Append `(row_id, status, computed_fingerprint, currently_set)` to an in-memory records list, where `currently_set` is the **stored** `row.content_fingerprint` (which may be NULL, may match `computed_fingerprint`, or may disagree — all three are possible).
5. Bucket the records by `computed_fingerprint`: `dict[fingerprint, list[Record]]`.
6. For each fingerprint bucket, determine the **write decision per row in the bucket**:
  - **Live-row collision** (≥2 records with `status IN ('candidate','active')`): mark all rows in the bucket as "do not write this run". Rows whose `currently_set` was already non-NULL stay as-is; rows whose `currently_set` was NULL stay NULL. Record the collision tuple `(fingerprint, [row_ids], memory_type, scope, subject)` for the summary.
  - **No live-row collision**: every row in the bucket gets a write decision per the idempotency rules:
    - If `currently_set` is NULL → "write `computed_fingerprint`".
    - If `currently_set == computed_fingerprint` and `--force` is not set → "skip (no-op)".
    - If `currently_set != computed_fingerprint` (stale stored value) and `--force` is set → "write `computed_fingerprint`" (overwrite the stale value).
    - If `currently_set != computed_fingerprint` (stale stored value) and `--force` is not set → "skip but log a warning" (the operator may have manually set this value; don't silently clobber; expose the discrepancy in the summary and exit non-zero).
    - Terminal rows follow the same rules but are never at risk of partial-unique-index conflict.

**Pass 2 (writes, single batch):**

1. Within the same transaction, issue one `UPDATE memories SET content_fingerprint = ? WHERE id = ?` per row with a "write" decision. Rows in live-vs-live collision buckets get no UPDATE.
2. `COMMIT`.

Exit with code 0 if `collisions` is empty, 2 otherwise.

This algorithm:

- **Is deterministic** (row order is `id ASC`; bucket classification depends only on freshly-computed content hashes — never on stored values).
- **Correctly handles re-runs where some rows already have fingerprints** — by always reading all live rows for Pass 1 bucketing regardless of idempotency filter, it catches the case where a previous run left row A with fingerprint F and row B as NULL with the same content, where a naïve "walk only NULL" approach would try to write F onto row B and fail the partial unique index.
- **Correctly handles stale/corrupt stored fingerprints** — bucketing recomputes from `payload_json` for every row, so a row with a manually-edited or buggy stored value cannot hide a real live-vs-live collision from the collision detector. Stale values surface in the summary and either block the backfill (no `--force`) or get overwritten (`--force`).
- **Never needs `SAVEPOINT`** — all decisions are computed in memory before any writes.

**Memory footprint — honest accounting.** Per-row Python object cost (rough):

- Record tuple: ~56 bytes
- 64-char hex fingerprint string: ~113 bytes (string header + UTF-8 body)
- Short status string (`"active"`, `"candidate"`, etc.): ~56 bytes (likely interned, amortized to near-zero after the first few rows)
- Int id: ~28 bytes
- Dict bucket entries + list references: ~50–80 bytes overhead per record

Conservative per-row cost: ~250 bytes. At 100k rows that's **roughly 25 MB**; at 10k rows (closer to expected single-user scale), ~2.5 MB. Both fine for single-process backfill. The plan doc measures actual scale before merge; if projected usage exceeds an arbitrary 100 MB threshold, the plan adds a `--max-rows N` flag that splits the backfill into explicit operator-driven sessions (which relaxes the single-transaction invariant and ships with a warning).

The operator resolves collisions by rejecting/expiring duplicates via MCP tools (which use the normal live-triple lookup, not the fingerprint), then re-running the backfill.

## 9) Performance

### 9.1 Write Cost

Fingerprint computation per insert: one `unicodedata.normalize` + one `casefold` + one `re.sub` + one `sha256` over <1KB of input. The additional runtime is dominated by `unicodedata.normalize` (hundreds of nanoseconds to a few microseconds depending on payload size) and is negligible compared to the existing `BEGIN IMMEDIATE` + `INSERT` cost.

### 9.2 Read Cost

The fingerprint lookup in `ingest_proposals` is a single indexed lookup against `idx_memories_content_fingerprint_all` — same cost class as the existing structural lookup. The two lookups run sequentially, not as a JOIN. Total overhead per proposal is "two indexed lookups worth" on top of what's already there. The fingerprint lookup pulls `payload_json` unconditionally; for the (common) rejected/no-match cases this is wasted bytes, but a single-row indexed lookup at single-user scale is dominated by fixed costs and the extra JSON bytes are not worth a per-branch query. Detectors currently emit a bounded number of proposals per snapshot, so per-snapshot overhead is well under the latency budget dominated by LLM calls.

### 9.3 Backfill Cost

Backfill is a single writer-lock transaction holding for the duration of reading all live + terminal rows plus per-row fingerprint compute. At the ~~10k-row scale used for the §8.4 memory budget, per-row fingerprint compute is dominated by Pydantic coercion (~~100 µs/row) not SHA-256 (~10 µs/row), giving a rough estimate of a few seconds end-to-end. Exact duration is a function of the table size at the operator's install, which will be measured by the plan doc before merge. Spec commits to the invariant: **single transaction, writer lock, operator stops writers during the run**. If the measured duration at then-current scale crosses an ops threshold (arbitrary: 30 seconds), the plan doc will add a `--max-rows N` circuit-breaker flag (see §8.4).

## 10) Test Plan

### 10.1 Unit Tests — `minx_mcp/core/fingerprint.py`

- Empty/None inputs → stable empty-parts fingerprint.
- NFC equivalence: decomposed vs composed Unicode → same fingerprint.
- Casefold correctness: `"ß"` vs `"ss"` → same fingerprint; `"ŞŞ"` vs `"şş"` → same fingerprint.
- Whitespace collapse: `"a   b"` vs `"a\tb"` vs `"a\n b"` → same fingerprint.
- Separator safety: `content_fingerprint("ab", "c") != content_fingerprint("a", "bc")`.
- Part-count edge cases matching §4.3:
  - `content_fingerprint()` == `content_fingerprint("")` (documented fixed point).
  - `content_fingerprint("", "")` != `content_fingerprint("")` (separator appears).
  - `content_fingerprint("a")` != `content_fingerprint("a", "")` (separator appears).

**Golden vectors (pinned for the life of the normalization contract; any change requires a new migration per §11):**


| Input (repr)                                                                     | Expected SHA-256                                                                                                                                      |
| -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `content_fingerprint()`                                                          | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (sha256 of `b""`)                                                                  |
| `content_fingerprint("")`                                                        | same as above (fixed point per §4.3)                                                                                                                  |
| `content_fingerprint("", "")`                                                    | sha256 of `b"\x00"` — computed and pinned in fixture                                                                                                  |
| `content_fingerprint("Netflix")`                                                 | must equal `content_fingerprint("netflix")`, `content_fingerprint("NETFLIX")`, `content_fingerprint(" netflix ")`, `content_fingerprint("net\tflix")` |
| `content_fingerprint("preference", "core", "Netflix", "prefer 4k", "always-on")` | must equal the same call with `"netflix"` in place of `"Netflix"` and `"prefer 4k"` in place of `"prefer 4k"`                                         |
| `content_fingerprint("café")` with NFC vs NFD é                                  | must be equal                                                                                                                                         |


The 64-char digests for the last three rows are computed when the implementation lands and written into the fixture file. The spec commits to the *equivalence relationships*; the fixture commits to the *digest values*. Together they catch normalization drift in either direction.

**Fixture file schema (`tests/fixtures/fingerprint_golden.json`):**

```json
{
  "version": 1,
  "description": "Pinned SHA-256 digests for minx_mcp.core.fingerprint.content_fingerprint. Any digest change MUST be accompanied by a re-fingerprinting migration per §11 of slice6g spec.",
  "vectors": [
    {
      "name": "empty_no_parts",
      "parts": [],
      "expected_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "name": "single_empty_part",
      "parts": [""],
      "expected_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    },
    {
      "name": "two_empty_parts",
      "parts": ["", ""],
      "expected_sha256": "<computed at implementation time — fill before merge>"
    }
  ]
}
```

**Placeholder behavior:** any vector whose `expected_sha256` equals the literal string `"<computed at implementation time — fill before merge>"` causes `tests/test_fingerprint_golden.py` to **fail** with a clear error message instructing the implementer to fill the digest. This prevents a placeholder fixture from shipping green.

`parts` is a JSON array of strings or nulls. The test loader decodes each entry, calls `content_fingerprint(*parts)`, and asserts the digest matches `expected_sha256`.

### 10.2 Integration Tests — `MemoryService`

- `create_memory` persists the fingerprint column (non-null for all known types).
- `create_memory` twice with different-casing subjects raises `ConflictError` with `conflict_kind="content_fingerprint"`, correct `memory_id`, and the stored `existing_subject`.
- `create_memory` with same triple as an existing live row raises `ConflictError` with `conflict_kind="structural_triple"` and `memory_id` (v5 shape evolution — `tests/test_memory_service.py:963–996` is updated per §6.2).
- `update_payload` that produces a fingerprint collision with another live row raises `ConflictError` with `conflict_kind="content_fingerprint_update"` and both `memory_id` and `blocking_memory_id`.
- `ingest_proposals` with two proposals differing only in subject case produces one memory row, one `created` event, one `payload_updated` event with `payload.merge_trigger = "content_fingerprint"` and `payload.prior_identity` capturing the suppressed triple.
- `ingest_proposals` content-equivalence merge promotes `candidate` → `active` when `max(prior_confidence, proposal_confidence) >= 0.8` and emits a `promoted` event alongside the `payload_updated` event. **This parallels the existing structural merge's promotion semantics.**
- `ingest_proposals` content-equivalence merge produces no UPDATE and no event when the merged payload, new confidence, new status, and reason all match the stored values (skip-write short-circuit).
- `ingest_proposals` with a proposal whose structural prior is `rejected` records a `MemoryProposalSuppression(reason="structural_rejected_prior")` in `report.suppressed`; `report.failures` remains empty for this case.
- `ingest_proposals` with an invalid payload whose structural prior is `rejected` records a `MemoryProposalSuppression(reason="structural_rejected_prior")` (**not** a `MemoryProposalFailure` — the rejected check runs before validation, preserving the existing suppress-over-fail ordering).
- `ingest_proposals` with a proposal matching a fingerprint-rejected prior (different triple, same content, rejected) records a `MemoryProposalSuppression(reason="content_fingerprint_rejected_prior")`; `report.failures` remains empty.
- `ingest_proposals` with a proposal matching an `expired` fingerprint creates a fresh row (existing expired-reopen contract, unchanged).
- `reject_memory` releases the fingerprint: a subsequent `create_memory` with the same content succeeds.
- `expire_memory` releases the fingerprint: same as reject.
- Per-type fingerprint input: construct one memory of each known type (`preference`, `pattern`, `entity_fact`, `constraint`) with populated per-type fields and assert the stored fingerprint matches `content_fingerprint(*_memory_fingerprint_input(...))` recomputed in-test.
- Unknown-type fallback: construct a memory with a made-up `memory_type` not in `PAYLOAD_MODELS`, assert the stored fingerprint equals `content_fingerprint(type, scope, subject, "", json.dumps(payload, sort_keys=True, ensure_ascii=False))`.
- Corrupted-payload fingerprint degradation: store a pre-6a-style row whose `payload_json` fails `coerce_prior_payload_to_schema`; assert the stored fingerprint equals `content_fingerprint(memory_type, scope, subject, "", "")` (§5.2 degraded-dedup path).
- Empty-content clean-row edge: create a `preference` with `value=None` and `note=None`; assert the stored fingerprint equals the fingerprint of a corrupted row with the same triple (§5.2 acknowledged collision). Assert that the partial unique index correctly blocks creating a second such row.
- Alias-order stability: two `entity_fact` rows with `aliases=["café", "NETFLIX"]` (NFC) and `aliases=["cafe\u0301", "netflix"]` (NFD, different case) fingerprint identically.
- **Structural/fingerprint same-row overlap (§7.2.2 step 6, `fp_match_same_triple` branch):** create a `candidate` memory, then call `ingest_proposals` with one proposal whose triple and content both match the existing row. Assert: exactly one memory row exists (no duplicate), `ingest_proposals` returns a report with `len(succeeded) == 1`, `report.failures == []`, `report.suppressed == []`, and the memory has exactly one `created` event plus at most one `payload_updated` event (zero if the skip-write short-circuit trips on identical merged payload / confidence / status / reason, one otherwise). Specifically asserts that the fingerprint lookup and the structural lookup finding the same row does not cause a double merge or duplicate events.
- **Content-equivalence merge takes precedence over the insert path (the load-bearing v5 fix):** create a live `active` memory at `(preference, core, Netflix)` with a given payload. Call `ingest_proposals` with a proposal at `(preference, core, netflix)` (different subject casing only; same fingerprint after normalization — `normalize_for_fingerprint` applies NFC + `casefold()`, so `"Netflix"` and `"netflix"` produce the same normalized subject part, and the payload is byte-identical). Assert: no new memory row is inserted (exactly one live row exists afterward, same id), the existing row's `payload_updated` event was emitted with `merge_trigger="content_fingerprint"` and `prior_identity={"memory_type":"preference","scope":"core","subject":"netflix"}`, `report.suppressed == []`, `report.failures == []`, and `len(report.succeeded) == 1`. Without v5's pre-fork fingerprint lookup this test would fail: the proposal would take the insert path (`row is None` for subject `"netflix"`), hit the partial fingerprint unique index, and surface as `ConflictError(conflict_kind="content_fingerprint")` instead of merging.

### 10.3 Ingest Report Tests

- `IngestProposalsReport` gains a `suppressed: list[MemoryProposalSuppression]` field; all existing tests that compare `report == [records]` still pass (dunder `__eq__(list)` unchanged).
- `IngestProposalsReport == IngestProposalsReport` equality (dataclass default) compares all three lists. Add a test for this to prevent regression.
- `snapshot.py` emits an info-level log (not a `PersistenceWarning`) for suppressions-only ingest runs.
- `snapshot.py` emits a `PersistenceWarning` labeled "skipped invalid proposals" for `failures`-containing runs (existing behavior preserved).
- An ingest run with both suppressions and failures emits both the info log and the warning, separately.

### 10.4 Backfill Tests

- Backfill on a DB with only live rows: all get fingerprints, no collisions, exit 0.
- Backfill on a DB with one live-vs-live collision: both rows remain NULL, exit 2, summary lists the colliding `memory_ids`.
- Backfill on a DB with live-vs-rejected collision: both rows get fingerprints (rejected doesn't compete on the partial index), exit 0.
- Backfill idempotence: second run is a no-op (0 rows written, exit 0).
- **Backfill re-run correctness (v4 fix):** set row A's `content_fingerprint` manually to `F`, leave row B with the same content and `content_fingerprint=NULL`. Run the backfill without `--force`. Assert row B is NOT updated (it would violate the partial unique index with A), and the run reports a live-vs-live collision involving {A, B} with exit 2.
- Backfill `--force`: second run overwrites existing fingerprints without raising (still respects the in-pass collision policy).
- Backfill with legacy payload containing unknown keys: the fingerprint is computed over the coerced form, not the raw form. Test constructs a pre-6a-style row with an unknown key in `payload_json`, then asserts the stored fingerprint equals `content_fingerprint(*_memory_fingerprint_input(type, coerce_prior_payload_to_schema(type, raw), scope=row_scope, subject=row_subject))`.
- Backfill with fully-corrupted payload: row whose `payload_json` coerces to `{}`; assert the stored fingerprint equals `content_fingerprint(memory_type, scope, subject, "", "")` (§5.2 degraded-dedup path).
- Backfill interrupt safety: simulate `KeyboardInterrupt` mid-pass; assert the `except BaseException` rolls back, assert a subsequent connection sees no `content_fingerprint` values written, assert the writer lock is released.

### 10.5 Migration Test

- Applying `020_memory_content_fingerprint.sql` to a fresh DB (full migration chain 001–020) produces a schema matching the canonical one.
- Applying `020` on top of a DB that stopped at `019_playbook_runs.sql` preserves all existing `memories` rows, events, and playbook runs.
- Applying `020` is observed to be one migration entry in `_migrations` with a stable checksum.
- `_validate_migration_paths` continues to pass (contiguous numbering 001–020).

### 10.6 Regression Gates

- `uv run mypy minx_mcp` — 0 errors.
- `uv run ruff check minx_mcp tests` — clean.
- `uv run pytest tests/ -x -q` — all existing tests still pass (including the updated `tests/test_memory_service.py` structural-triple assertion); new tests pass.
- `tests/test_db.py::test_built_wheel_includes_packaged_resources` still passes (no packaging regression).
- `tests/test_hermes_http_stack_smoke` still passes (no MCP contract regression).
- New: `tests/test_fingerprint_golden.py` loads `tests/fixtures/fingerprint_golden.json` and asserts the primitive produces the pinned digests. Regression gate against silent normalization drift. Rejects placeholder sentinels per §10.1.

## 11) Forward Compatibility — Pattern for Future Consumers

Slice 6h–6l each add a new table or augment an existing one. Each must:

1. Import `content_fingerprint` from `minx_mcp/core/fingerprint.py`.
2. Define its own `_fingerprint_input(...)` helper in the service that owns the table (not in `fingerprint.py`).
3. Document the input part list in a module-level constant or docstring, naming which payload fields participate and why the excluded fields are excluded.
4. Add a `content_fingerprint TEXT` column and a partial unique index scoped to "live" rows (definition of "live" is per-table: unexpired for memory/journal, non-superseded for edges, etc.).
5. Add a backfill script mirroring this slice's script, with the same single-transaction writer-lock discipline and `try/except BaseException` rollback-on-interrupt pattern.
6. If payload schema evolution changes the input part list, the same PR must update the backfill and add a migration that re-populates the fingerprint column. No silent drift.
7. If a new memory type is added to `PAYLOAD_MODELS`, the §5.2 table in this spec must be extended in the same PR with an explicit `payload_value_part` source for that type. The fallback for unknown types is safe-but-degraded (see §5.2), so the window between a new type landing in a detector and the mapping being registered ships safely but with reduced dedup precision.
8. Consumers that construct and compare `IngestProposalsReport` instances directly should be aware that dataclass default `__eq__` compares `succeeded`, `failures`, and `suppressed` — not just `succeeded`. The existing `__eq__(list)` override still only compares `succeeded`.

Rules 6 and 7 are the load-bearing ones. They make the fingerprint part list a documented API, not an implementation detail.

## 12) Rollback

Slice 6g is additive — it only adds a nullable column, two indexes, a script, a new dataclass, a new `IngestProposalsReport` field, and a snapshot info-log branch. Rollback is:

1. `DROP INDEX idx_memories_content_fingerprint_live`
2. `DROP INDEX idx_memories_content_fingerprint_all`
3. `ALTER TABLE memories DROP COLUMN content_fingerprint`
4. Revert the service-layer code changes (enumerated to match all v6 signature additions):
  - Revert `MemoryService._insert_memory_and_events`: drop the `fingerprint: str` kwarg; drop `content_fingerprint` from the INSERT column list; revert the `SELECT id` probe back to pre-6g's two-column `ConflictError.data` shape (removes `conflict_kind` and `memory_id` keys).
  - Revert `MemoryService.create_memory`: drop the inline `fp = _fp_compute(*_memory_fingerprint_input(memory_type, payload, scope=scope, subject=subject))` computation and the `fingerprint=fp` kwarg pass-through.
  - Revert `MemoryService.ingest_proposals`: drop the fingerprint computation at step 4, the fingerprint lookup at step 5, the content-equivalence merge branch (§7.2.3, including its `BEGIN IMMEDIATE` wrapper), the unified rejected-suppression path (structural and fingerprint), and `suppressed` list population. Revert the docstring at `memory_service.py:422–425` back to describing rejected-prior proposals as "silently suppressed" / "omitted from the returned list".
  - Revert `MemoryService.update_payload` to its pre-6g form (drop the fingerprint recomputation, the `content_fingerprint = ?` column in the UPDATE, and the `IntegrityError` handler introduced by §7.3).
  - Revert `_memory_fingerprint_input` from its v6 canonical signature `(memory_type, payload, *, scope, subject)` back to its pre-6g signature `(memory_type, payload)` — removing both required kwargs. Also remove `_canonical_aliases` and the per-type mapping table.
  - Remove the `MemoryProposalSuppression` dataclass.
  - Remove the `suppressed` field from `IngestProposalsReport` and revert its `__init__` callsite on the merge path (no longer needs a `suppressed=[]` default).
  - Revert `minx_mcp/core/snapshot.py` to drop the suppressions info-log branch.
  - Revert `tests/test_memory_service.py:963–996` structural-triple assertion to its pre-6g three-key form (matching `_insert_memory_and_events`'s handler revert); drop the new same-row overlap and pre-fork content-equivalence tests added by §10.2.
  - Revert `tests/test_core_memory_tools.py:142–174` MCP-tool assertion to its pre-6g three-key form (drop the `first_id` capture at `first["data"]["memory"]["id"]`; restore the three-key dict).
5. Revert the Slice 9 spec's migration-number update (`021` → `020` in its `## 5) Schema (Core)` section and in the phase-breakdown table).
6. Remove `scripts/backfill_memory_fingerprints.py`, `minx_mcp/core/fingerprint.py`, `tests/fixtures/fingerprint_golden.json`, `tests/test_fingerprint_golden.py`.
7. Revert `HANDOFF.md` entries added by this slice:
  - Remove the implemented-slices row for Slice 6g.
  - Remove the "Run `uv run python -m scripts.backfill_memory_fingerprints` after migration 020" operator-post-migration step.
  - Remove any cross-references from Slice 6h–6l's planning entries that pointed at this slice as a shared primitive (they'll be re-added when this slice re-lands).

SQLite supports `DROP COLUMN` as of 3.35 (indexes on the column must be dropped first, which steps 1 and 2 handle). The migration checksum guard (`minx_mcp/db.py::apply_migrations`) will catch any attempt to re-apply a modified `020_memory_content_fingerprint.sql` without a rename, following the operator step already documented in migration 018's header comment.

## 13) Out of Scope

- Secret scanning on subject/payload fields (**Slice 6h**) — adds write-path detection of credentials and typed `SecretDetectedError`. 6h also introduces the vault frontmatter fingerprint consumer since scanning frontmatter for secrets naturally overlaps with fingerprinting it for content identity.
- FTS5 full-text search over memory content (**Slice 6i**) — adds the `memories_fts` virtual table and trigger-synced content shadow.
- Memory graph edges (**Slice 6j**) — adds `memory_edges` for typed semantic relationships (supersedes/contradicts/supports/cites).
- Async enrichment queue (**Slice 6k**) — adds `enrichment_queue` + sweep playbook for offloading background work.
- Embeddings + semantic retrieval (**Slice 6l**) — adds `memory_embeddings` BLOB column populated via the enrichment queue.
- Journal entry fingerprint (Slice 7 when it lands) — consumer of the primitive.
- Investigation step fingerprint (Slice 9c) — consumer of the primitive.
- Retrofitting the finance dedupe script to use `except BaseException` rollback (Slice 6m cleanup).
- Cross-table fingerprint indexing (e.g. "find this memory's content in the vault") — the shared primitive makes this trivially expressible as a query later, but no new infrastructure is needed for it.

## 14) Verification Checklist

- Slice 9 spec (`docs/superpowers/specs/2026-04-19-slice9-agentic-investigations.md`) updated from `020_investigations.sql` to `021_investigations.sql` in both its `## 5) Schema (Core)` section and its phase-breakdown table.
- `minx_mcp/core/fingerprint.py` exists with the two public functions, docstrings matching §4, and the exact bytestring formula from §4.3.
- `tests/fixtures/fingerprint_golden.json` exists with the schema defined in §10.1, pinned digests for listed vectors, and no remaining placeholder sentinels.
- `tests/test_fingerprint_golden.py` fails on any placeholder sentinel and on any digest mismatch.
- `minx_mcp/schema/migrations/020_memory_content_fingerprint.sql` matches §5.1 exactly (raw `ALTER TABLE`, two `CREATE INDEX IF NOT EXISTS`, no `add_column_if_missing` invocation).
- `MemoryService._memory_fingerprint_input` has the v6 canonical signature `(memory_type, payload, *, scope, subject)` — both `scope` and `subject` are required kwargs. Implements the per-type mapping from §5.2 for all four known types + unknown-type fallback. `_canonical_aliases` (same module) normalizes each alias before sorting.
- Every caller of `_memory_fingerprint_input` passes `scope` and `subject` explicitly: `create_memory` uses `scope=scope, subject=subject` (its own kwargs), `ingest_proposals` step 4 uses `scope=proposal.scope, subject=proposal.subject`, `update_payload` uses `scope=str(row["scope"]), subject=str(row["subject"])`, and the backfill uses `scope=row["scope"], subject=row["subject"]`.
- `MemoryService._insert_memory_and_events` **receives** the fingerprint as a required `fingerprint: str` kwarg (callers compute it; the helper does not). Persists `fingerprint` in the `content_fingerprint` column, and discriminates `IntegrityError` per §7.1 using `SELECT id` for the structural check and `SELECT id, subject` for the fingerprint check.
- Structural-triple `ConflictError` carries `conflict_kind="structural_triple"` and `memory_id` per §6.1.
- Content-fingerprint `ConflictError` carries `conflict_kind="content_fingerprint"`, `memory_id`, `existing_subject` per §6.1.
- Content-fingerprint-update `ConflictError` carries `conflict_kind="content_fingerprint_update"`, `memory_id`, `blocking_memory_id` per §6.1.
- `tests/test_memory_service.py::test_create_memory_duplicate_live_triple_raises_conflict` (lines 963–996) updated to the explicit before→after form shown in §6.2 (assignment added, 5-key dict asserted). The adjacent `test_create_memory_conflict_detection_ignores_unrelated_integrity_errors` (line 999+) is **not** edited — it does not assert on `ConflictError.data`.
- `tests/test_core_memory_tools.py::test_memory_create_duplicate_live_triple_returns_conflict` (lines 142–174) updated to the explicit before→after form shown in §6.2 (first-insert id captured from `first["data"]["memory"]["id"]`, 5-key dict asserted). The MCP tool serialization path (`minx_mcp/core/tools/memory.py`) passes `ConflictError.data` through verbatim, so no tool-code change is needed.
- `IngestProposalsReport` gains a `suppressed: list[MemoryProposalSuppression]` field; `MemoryProposalSuppression` dataclass added; existing `__eq__(list)` unchanged.
- `ingest_proposals` control flow per §7.2.2: structural lookup → rejected-check-first → validate → fingerprint lookup → dispatch. The fingerprint lookup runs **before** the insert/merge fork, not inside the merge branch, so a cross-triple fingerprint match routes to the content-equivalence merge (§7.2.3) from either the `row is None` / `row.status == "expired"` path or the live-prior path. Rejected-prior populates `suppressed`, not `failures`, in both the structural and fingerprint paths. Invalid payload on rejected subject still suppresses (pre-6g behavior preserved).
- `ingest_proposals` docstring at `memory_service.py:413–444` updated: the `Rejected prior` bullet no longer says "silently suppressed" or "omitted from the returned list". It now says "recorded in `IngestProposalsReport.suppressed` with `reason="structural_rejected_prior"`; snapshot emits an info-level log but not a warning". A new bullet documents the fingerprint-rejected suppression path (`reason="content_fingerprint_rejected_prior"`).
- Content-equivalence merge per §7.2.3: inherits `max` confidence, candidate→active promotion, `promoted` event emission, skip-write short-circuit, `reason` column update, and `BEGIN IMMEDIATE` + rowcount guarding from the existing merge. Deltas: updates the fingerprint-matched row (not the proposal's triple) and augments the `payload_updated` event with `merge_trigger` + `prior_identity`.
- `update_payload` per §7.3: recomputes the fingerprint over the new payload (passing `scope=str(row["scope"])` and `subject=str(row["subject"])` into `_memory_fingerprint_input`'s required kwargs), includes `content_fingerprint = ?` in the UPDATE, and wraps the write in an `IntegrityError` handler that probes for the blocking row. Raises `ConflictError` with `conflict_kind="content_fingerprint_update"`, `memory_id`, `blocking_memory_id` on content collision; re-raises `IntegrityError` when the probe finds no blocking row (reclassifies as INTERNAL_ERROR at the MCP boundary).
- Fingerprint is always computed over the coerced payload (§5.2 invariant) — enforced in write paths and in the backfill script.
- Corrupted-row and empty-content edge behavior documented and tested per §5.2 and §10.2.
- `snapshot.py` emits a separate info-level log for `report.suppressed`; the existing `PersistenceWarning` for `report.failures` is unchanged.
- `scripts/backfill_memory_fingerprints.py` uses single-transaction writer lock, two-pass algorithm per §8.4 (reads **all** live rows for bucketing regardless of idempotency filter), `try/except BaseException` + rollback on interrupt per §8.1, and exit codes per §8.
- All unit, integration, ingest-report, backfill, and migration tests from §10 pass.
- `tests/test_fingerprint_golden.py` passes on first run (no digest drift).
- `HANDOFF.md` gains a new "Post-Upgrade Operator Steps" entry (§8.2 wording) and a new "Implemented Slices" row.
- `uv run pytest` / `uv run mypy minx_mcp` / `uv run ruff check` all green.

