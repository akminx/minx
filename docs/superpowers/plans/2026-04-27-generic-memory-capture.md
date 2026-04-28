# Generic Memory Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Core MCP tool `memory_capture` that stores quick, review-first captures as `captured_thought` memories via `MemoryService.create_memory`, returns structured render hints for Hermes, extends FTS5 to index `payload_json.text` and `capture_type`, and documents operator workflow (candidate status, search filters, rebuild script).

**Architecture:** MCP tool code in `minx_mcp/core/tools/memory.py` validates inputs, builds a permissive payload (`text`, `capture_type`, optional `metadata`), derives or normalizes `subject`, rejects `confidence >= 0.8`, then calls `create_memory` with `memory_type="captured_thought"` and `actor="user"`. The tool returns the stored memory plus `response_template` / `response_slots` so Hermes owns final conversational wording. Deterministic normalization and metadata shape/size checks live in a small pure module `minx_mcp/core/memory_capture.py` so tests can import them without spinning up FastMCP. FTS behavior is updated by migration `026_memory_capture_fts.sql` (same trigger pattern as `025_memory_fts_aliases.sql`) plus matching `scripts/rebuild_memory_fts.py` SQL. `captured_thought` is intentionally omitted from `PAYLOAD_MODELS` so `validate_memory_payload` passes the dict through unchanged.

**Tech Stack:** Python 3.12, SQLite + FTS5, `mcp` / FastMCP tools, existing `MemoryService`, `scan_memory_input` / `validate_memory_payload`, pytest, ruff, mypy.

---

## File map

| File | Role |
|------|------|
| `minx_mcp/core/memory_capture.py` | **Create:** pure functions: `normalize_capture_type`, `validate_capture_metadata`, `derive_capture_subject`, `build_captured_thought_payload`, `build_capture_response_slots`, `normalize_capture_text_for_body`. |
| `minx_mcp/core/tools/memory.py` | **Modify:** register `@mcp.tool(name="memory_capture")`, implement `_memory_capture` calling helpers + `MemoryService.create_memory` with `reason=""`. |
| `minx_mcp/core/memory_payloads.py` | **Modify:** add a short comment that `captured_thought` is intentionally omitted from `PAYLOAD_MODELS`. |
| `minx_mcp/schema/migrations/026_memory_capture_fts.sql` | **Create:** drop/recreate `memories_ai_fts`, `memories_au_fts`, `memories_ad_fts` with extended `payload_text` CASE. |
| `scripts/rebuild_memory_fts.py` | **Modify:** extend `CASE` in `INSERT ... SELECT` to match migration. |
| `tests/test_core_memory_tools.py` | **Modify:** register `memory_capture` in round-trip tool list; add tool-focused tests. |
| `tests/test_memory_service.py` | **Modify:** deterministic helper tests + `captured_thought` service round-trip with `status="candidate"` search. |
| `tests/test_rebuild_memory_fts.py` | **Modify:** FTS finds unique token in `captured_thought` `payload.text` after rebuild. |
| `README.md` | **Modify:** "Quick capture vs structured create" subsection. |
| `HANDOFF.md` | **Modify:** migration `026` + `rebuild_memory_fts.py` rollout note. |

---

### Task 1: Migration `026_memory_capture_fts.sql`

**Files:**
- Create: `minx_mcp/schema/migrations/026_memory_capture_fts.sql`
- Test: `tests/test_rebuild_memory_fts.py` (Task 2 will assert behavior; migration must exist first)

- [ ] **Step 1: Add migration file**

Copy the structure from `minx_mcp/schema/migrations/025_memory_fts_aliases.sql`. Keep the same three triggers (`memories_ai_fts`, `memories_au_fts`, `memories_ad_fts`). Replace only the inner `CASE` expression so each branch concatenates, in order, with spaces:

`$.value`, `$.note`, `$.signal`, `$.limit_value`, `$.aliases`, `$.text`, `$.capture_type`

using the pattern from the spec:

```sql
COALESCE(json_extract(new.payload_json, '$.value'), '') || ' ' ||
COALESCE(json_extract(new.payload_json, '$.note'), '') || ' ' ||
COALESCE(json_extract(new.payload_json, '$.signal'), '') || ' ' ||
COALESCE(json_extract(new.payload_json, '$.limit_value'), '') || ' ' ||
COALESCE(json_extract(new.payload_json, '$.aliases'), '') || ' ' ||
COALESCE(json_extract(new.payload_json, '$.text'), '') || ' ' ||
COALESCE(json_extract(new.payload_json, '$.capture_type'), '')
```

This key-based extraction intentionally applies to any future memory type that stores canonical `payload.text` or `payload.capture_type`, not only `captured_thought`.

Use `new.payload_json` in `INSERT` trigger and `new.payload_json` in `UPDATE` trigger (mirror `025` exactly). Header comment should mention capture FTS and that existing DBs should run `python -m scripts.rebuild_memory_fts <db>` after upgrade.

- [ ] **Step 2: Sanity check migration is picked up**

Run: `python -c "from pathlib import Path; from minx_mcp.db import migration_dir; print(sorted(p.name for p in migration_dir().glob('*.sql')))"`

Expected: stdout includes `026_memory_capture_fts.sql` after `025_memory_fts_aliases.sql`.

- [ ] **Step 3: Fresh DB applies cleanly**

Run: `python -c "from pathlib import Path; import tempfile; from minx_mcp.db import get_connection; p=Path(tempfile.mkdtemp())/'t.db'; get_connection(p).close()"`

Expected: no exception (all migrations including `026` apply).

- [ ] **Step 4: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 2: `scripts/rebuild_memory_fts.py`

**Files:**
- Modify: `scripts/rebuild_memory_fts.py` (the `CASE` inside `INSERT INTO memory_fts ... SELECT`)

- [ ] **Step 1: Write failing test**

In `tests/test_rebuild_memory_fts.py`, add `test_rebuild_memory_fts_indexes_captured_thought_text(tmp_path) -> None`:

```python
def test_rebuild_memory_fts_indexes_captured_thought_text(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    svc = _service_for(db_path)
    unique = "xenonbravo_capture_token_91357"
    record = svc.create_memory(
        memory_type="captured_thought",
        scope="core",
        subject="note:hello",
        confidence=0.5,
        payload={"text": f"Remember to find {unique} in FTS.", "capture_type": "observation"},
        source="user:test",
        reason="",
    )
    svc.conn.execute("DELETE FROM memory_fts WHERE rowid = ?", (record.id,))
    svc.conn.commit()
    assert svc.search_memories(query=unique, status="candidate") == []

    assert main([str(db_path)]) == 0

    hits = svc.search_memories(query=unique, status="candidate")
    assert [r.memory.id for r in hits] == [record.id]
```

- [ ] **Step 2: Run test - expect FAIL**

Run: `pytest tests/test_rebuild_memory_fts.py::test_rebuild_memory_fts_indexes_captured_thought_text -v`

Expected: **FAIL** - either empty search after rebuild (script not yet updated) or failure earlier if migration/triggers missing.

- [ ] **Step 3: Implement - extend `CASE` in `rebuild_memory_fts`**

Match the same seven-field concatenation as in `026_memory_capture_fts.sql`, using `payload_json` column in the `SELECT` (not `new.`).

- [ ] **Step 4: Run test - expect PASS**

Run: `pytest tests/test_rebuild_memory_fts.py::test_rebuild_memory_fts_indexes_captured_thought_text -v`

Expected: **PASS**

- [ ] **Step 5: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 3: Pure capture helpers (`minx_mcp/core/memory_capture.py`)

**Files:**
- Create: `minx_mcp/core/memory_capture.py`
- Test: `tests/test_memory_service.py`

Constants (single source for tests and implementation):

- `CAPTURE_TYPE_MAX_BYTES = 64`
- `SUBJECT_MAX_BYTES = 200`
- `METADATA_MAX_TOP_LEVEL_KEYS = 32`
- `METADATA_MAX_DEPTH = 4`
- `METADATA_STRING_MAX_BYTES = 4096` (4 KiB UTF-8)

Behavior (lock to spec):

1. **`normalize_capture_text_for_body(text: str) -> str`**

   - Strip leading/trailing whitespace; collapse internal runs of whitespace (including newlines) to a single ASCII space for the **stored body** `text` field.
   - If empty after normalization, callers raise `InvalidInputError` for `text` (not in this function - document that contract).

2. **`normalize_capture_type(raw: str) -> str`**

   - Strip `raw`. If empty, return `"observation"`.
   - Lowercase ASCII letters.
   - Replace interior whitespace runs with `_`.
   - Allowed characters are `[a-z0-9_-]`; replace each contiguous run of disallowed characters with `_`, collapse repeated `_`, then strip leading/trailing `_`.
   - If sanitizing leaves an empty string, return `"observation"`.
   - Enforce max **64 UTF-8 bytes**: if longer, truncate on a character boundary so final encoded length is <= 64; if truncation happened, append `"..."` within the byte limit.

3. **`derive_capture_subject(*, capture_type_normalized: str, raw_text: str, explicit_subject: str | None) -> str`**

   - If `explicit_subject` is not `None`: strip it; if empty, raise `InvalidInputError("subject must be non-empty")`. Apply `SUBJECT_MAX_BYTES` truncation with the same `...` suffix rule as the spec.
   - If `explicit_subject` is `None`: split `raw_text` into lines, use the first non-empty line after trimming, and collapse whitespace runs within that line to one ASCII space. If no non-empty line exists, use the literal `"capture"`. Prefix `f"{capture_type_normalized}:{fragment}"`. Apply the same 200-byte UTF-8 truncation with `...` when truncation occurs.
   - Stored `payload.text` is normalized separately with `normalize_capture_text_for_body(raw_text)`; subject derivation must not use the fully collapsed stored body for multi-line input.

   Implement shared `_truncate_utf8_with_ellipsis(value: str, max_bytes: int) -> str` used by both subject and capture_type.

4. **`validate_capture_metadata(meta: object) -> dict[str, object] | None`**

   - If `meta is None`: return `None`.
   - If `meta` is `{}` after normalization: return `None` (omit from payload).
   - Require `isinstance(meta, dict)`; else `InvalidInputError("metadata must be a JSON object")`.
   - Top-level key count <= 32; if not, `InvalidInputError` with clear message.
   - Recursively walk values: **max nesting depth 4** means at most **four** nested container layers along any path from the metadata root (treat the root dict as depth **1**; entering a nested `dict` or `list` increments depth; if the next nested container would reach depth **5**, raise `InvalidInputError`). Scalars (including strings) do not add a layer. Apply the same rule to each element of lists.
   - Every `str` value anywhere in the tree: UTF-8 byte length <= 4096 or `InvalidInputError`.
   - Keys must be strings; reject non-str keys with `InvalidInputError`.
   - Do **not** flatten metadata into FTS (no code change - documentation only).

5. **`build_captured_thought_payload(*, text: str, capture_type: str, metadata: dict[str, object] | None) -> dict[str, object]`**

   - Return `{"text": text, "capture_type": capture_type}` and if `metadata` is not `None`, add `"metadata": metadata`.

6. **`build_capture_response_slots(*, record: MemoryRecord, capture_type: str) -> dict[str, object]`**

   - Return only structured data Hermes can render:

```python
{
    "memory_id": record.id,
    "status": record.status,
    "memory_type": record.memory_type,
    "scope": record.scope,
    "subject": record.subject,
    "capture_type": capture_type,
}
```

   - Do not include prose like `"I saved that"`; Hermes owns final wording.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_memory_service.py`:

```python
from minx_mcp.core.memory_capture import (
    build_capture_response_slots,
    derive_capture_subject,
    normalize_capture_type,
    validate_capture_metadata,
)

def test_normalize_capture_type_empty_becomes_observation() -> None:
    assert normalize_capture_type("") == "observation"
    assert normalize_capture_type("   ") == "observation"


def test_normalize_capture_type_sanitizes_and_lowercases() -> None:
    assert normalize_capture_type("  Foo Bar  ") == "foo_bar"
    assert normalize_capture_type("Type-A/B") == "type-a_b"


def test_derive_capture_subject_stable_prefix_and_truncation() -> None:
    ct = normalize_capture_type("observation")
    ntext = "  Buy milk tomorrow  \nsecond line ignored  "
    subj = derive_capture_subject(
        capture_type_normalized=ct,
        raw_text=ntext,
        explicit_subject=None,
    )
    assert subj == "observation:Buy milk tomorrow"


def test_validate_capture_metadata_rejects_deep_nesting() -> None:
    bad = {"a": {"b": {"c": {"d": {"e": "too deep"}}}}}
    import pytest
    from minx_mcp.contracts import InvalidInputError

    with pytest.raises(InvalidInputError):
        validate_capture_metadata(bad)


def test_validate_capture_metadata_rejects_long_string_leaf() -> None:
    from minx_mcp.contracts import InvalidInputError
    import pytest

    bad = {"k": "x" * 5000}
    with pytest.raises(InvalidInputError):
        validate_capture_metadata(bad)


def test_build_capture_response_slots_returns_render_data_only() -> None:
    from minx_mcp.core.memory_models import MemoryRecord

    record = MemoryRecord(
        id=7,
        memory_type="captured_thought",
        scope="core",
        subject="observation:Buy milk",
        confidence=0.5,
        status="candidate",
        payload={"text": "Buy milk", "capture_type": "observation"},
        source="user:capture",
        reason="",
        created_at="2026-04-28T00:00:00Z",
        updated_at="2026-04-28T00:00:00Z",
        last_confirmed_at=None,
        expires_at=None,
    )
    assert build_capture_response_slots(record=record, capture_type="observation") == {
        "memory_id": 7,
        "status": "candidate",
        "memory_type": "captured_thought",
        "scope": "core",
        "subject": "observation:Buy milk",
        "capture_type": "observation",
    }
```

Use these exact helper names from `minx_mcp/core/memory_capture.py`.

- [ ] **Step 2: Run tests - expect FAIL**

Run: `pytest tests/test_memory_service.py::test_normalize_capture_type_empty_becomes_observation tests/test_memory_service.py::test_derive_capture_subject_stable_prefix_and_truncation -v`

Expected: **FAIL** (ImportError or missing module).

- [ ] **Step 3: Implement `minx_mcp/core/memory_capture.py`**

Implement all functions with `InvalidInputError` from `minx_mcp.contracts`.

- [ ] **Step 4: Run full new helper tests - expect PASS**

Run: `pytest tests/test_memory_service.py -k "normalize_capture_type or derive_capture_subject or validate_capture_metadata" -v`

Expected: **PASS**

- [ ] **Step 5: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 4: MCP tool `memory_capture`

**Files:**
- Modify: `minx_mcp/core/tools/memory.py`

- [ ] **Step 1: Write failing integration tests**

In `tests/test_core_memory_tools.py`:

1. Extend the tool enumeration in `test_memory_tools_round_trip` to include `"memory_capture"` in the `for name in (...)` tuple (place near `"memory_create"`).

2. Add:

```python
def test_memory_capture_happy_path_candidate(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    cap = get_tool(server, "memory_capture").fn
    out = cap("Pick up laundry after 5pm", "observation", "core", None, "user:capture", 0.5, None)
    assert out["success"] is True
    mem = out["data"]["memory"]
    assert mem["memory_type"] == "captured_thought"
    assert mem["status"] == "candidate"
    assert mem["confidence"] == 0.5
    assert out["data"]["response_template"] == "memory_capture.created_candidate"
    assert out["data"]["response_slots"] == {
        "memory_id": mem["id"],
        "status": "candidate",
        "memory_type": "captured_thought",
        "scope": "core",
        "subject": mem["subject"],
        "capture_type": "observation",
    }
    payload = mem["payload_json"]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert payload["text"] == "Pick up laundry after 5pm"
    assert payload["capture_type"] == "observation"
    assert "metadata" not in payload


def test_memory_capture_rejects_high_confidence(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    cap = get_tool(server, "memory_capture").fn
    out = cap("x", "observation", "core", None, "user:capture", 0.8, None)
    assert out["success"] is False
    assert out["error_code"] == "INVALID_INPUT"


def test_memory_capture_secret_blocked_like_create(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    cap = get_tool(server, "memory_capture").fn
    secret = _fake_private_key_block()
    out = cap(secret, "observation", "core", None, "user:capture", 0.5, None)
    assert out["success"] is False
    assert out["error_code"] == "INVALID_INPUT"
    assert secret not in str(out)


def test_memory_capture_derived_subject_stable(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    cap = get_tool(server, "memory_capture").fn
    a = cap("  hello  \nworld", "Note", "core", None, "user:capture", 0.5, None)
    b = cap("  hello  \nworld", "Note", "core", None, "user:capture", 0.5, None)
    assert a["success"] and b["success"]
    assert a["data"]["memory"]["subject"] == b["data"]["memory"]["subject"]
```

The positional order in the test must match the implemented FastMCP function signature shown in Step 3.

3. Add metadata and optional subject tests:

```python
def test_memory_capture_with_metadata_and_explicit_subject(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    cap = get_tool(server, "memory_capture").fn
    out = cap(
        "body text",
        "todo",
        "core",
        "my_subject",
        "user:capture",
        0.4,
        {"src": "chat"},
    )
    assert out["success"] is True
    mem = out["data"]["memory"]
    assert mem["subject"] == "my_subject"
    payload = mem["payload_json"]
    if isinstance(payload, str):
        import json
        payload = json.loads(payload)
    assert payload["metadata"] == {"src": "chat"}
```

- [ ] **Step 2: Run tests - expect FAIL**

Run: `pytest tests/test_core_memory_tools.py::test_memory_capture_happy_path_candidate -v`

Expected: **FAIL** - tool missing or wrong signature.

- [ ] **Step 3: Implement tool**

In `register_memory_tools`:

```python
    @mcp.tool(name="memory_capture")
    def memory_capture_tool(
        text: str,
        capture_type: str = "observation",
        scope: str = "core",
        subject: str | None = None,
        source: str = "user:capture",
        confidence: float | int = 0.5,
        metadata: object | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_capture(
                config,
                text,
                capture_type,
                scope,
                subject,
                source,
                confidence,
                metadata,
            ),
            tool_name="memory_capture",
        )
```

Implement `_memory_capture`:

1. `normalized_text = normalize_capture_text_for_body(text)`; if empty, raise `InvalidInputError("text must be non-empty")`.
2. `ct = normalize_capture_type(capture_type)`.
3. `meta = validate_capture_metadata(metadata)`.
4. `conf = _coerce_confidence(confidence)` then if `conf >= 0.8`, raise `InvalidInputError("confidence must be below 0.8 for memory_capture; use memory_create for active memories")`.
5. `sj = derive_capture_subject(capture_type_normalized=ct, raw_text=text, explicit_subject=subject)`.
6. `payload = build_captured_thought_payload(text=normalized_text, capture_type=ct, metadata=meta)`.
7. `require_non_empty("scope", scope)`, `require_non_empty("source", source)`.
8. `record = MemoryService.create_memory(memory_type="captured_thought", scope=scope, subject=sj, confidence=conf, payload=payload, source=source, reason="", actor="user")`.
9. `slots = build_capture_response_slots(record=record, capture_type=ct)`.
10. Return `{"memory": memory_record_as_dict(record), "response_template": "memory_capture.created_candidate", "response_slots": slots}`.

Tool **description** docstring (first line + body MCP uses): state defaults (`observation`, `core`, `user:capture`, `confidence=0.5`), that rows are **candidate** until `memory_confirm`, that `memory_search` defaults to `status="active"` so reviewers must pass `status="candidate"` (or `null` for both) to find captures, that `confidence` must stay below `0.8`, and that harnesses should render acknowledgement copy from `response_template` / `response_slots`.

Update module docstring at top of `memory.py` to mention `memory_capture`.

- [ ] **Step 4: Run tool tests - expect PASS**

Run: `pytest tests/test_core_memory_tools.py -k "memory_capture" -v`

Expected: **PASS**

- [ ] **Step 5: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 5: `PAYLOAD_MODELS` guard + service round-trip

**Files:**
- Modify: `minx_mcp/core/memory_payloads.py` (optional one-line comment)  
- Modify: `tests/test_memory_service.py`

- [ ] **Step 1: Add explicit comment in `memory_payloads.py`**

Above `PAYLOAD_MODELS`, add a short comment: `captured_thought` is intentionally omitted so permissive unknown-type validation applies.

- [ ] **Step 2: Write service round-trip test**

```python
def test_captured_thought_round_trip_list_and_search_candidate(tmp_path) -> None:
    svc = _fresh_memory_service(tmp_path)
    token = "uniquewxyzzy913"
    rec = svc.create_memory(
        memory_type="captured_thought",
        scope="core",
        subject="observation:test",
        confidence=0.5,
        payload={"text": f"note about {token}", "capture_type": "observation"},
        source="user",
        reason="",
    )
    listed = svc.list_memories(status="candidate", memory_type="captured_thought")
    assert any(r.id == rec.id for r in listed)
    hits = svc.search_memories(query=token, status="candidate", memory_type="captured_thought")
    assert [h.memory.id for h in hits] == [rec.id]
```

- [ ] **Step 3: Run test**

Run: `pytest tests/test_memory_service.py::test_captured_thought_round_trip_list_and_search_candidate -v`

Expected: **PASS** (FTS may already index via triggers once migration exists).

- [ ] **Step 4: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 6: Vault projection check (no code unless broken)

**Files:**
- Verify only: `minx_mcp/core/templates/wiki/memory.md`, reconciler paths

- [ ] **Step 1: Confirm no type-specific template is required**

Search: `rg 'captured_thought' minx_mcp/core` - expect **no** hits before this feature; after implementation, only memory tool/payload references.

The generic `minx-memory` wiki template uses `payload_json` as a blob; `captured_thought` validates permissively. If DB-to-vault export later special-cases types, that is out of scope unless an existing code path fails for unknown payloads. If any test or manual smoke shows reconciler rejecting `captured_thought`, file a follow-up (not in this plan).

- [ ] **Step 2: No commit** (unless a real bug is found)

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`
- Modify: `HANDOFF.md`

- [ ] **Step 1: README subsection**

Add **"Quick capture vs structured create"** under the README memory section near **Memory Embeddings**:

- `memory_capture`: fast, default **candidate**, `captured_thought` type; confirm with `memory_confirm`.
- `memory_create`: structured types, can set **active** at high confidence.
- Mention `memory_search` default `status="active"` - use `status="candidate"` for captures.
- Mention `response_template` / `response_slots` as the preferred Hermes rendering contract; Core stores and returns render data, Hermes owns final acknowledgement wording.

- [ ] **Step 2: HANDOFF rollout note**

- After deploy, run migrations (automatic on `get_connection` fresh apply; existing servers need app restart / migrate path per your ops).
- Run `python -m scripts.rebuild_memory_fts /path/to/minx.db` so pre-existing `captured_thought` rows pick up new FTS columns.

- [ ] **Step 3: Review checkpoint**

Pause for review. Do not commit unless the user explicitly asks for a commit in the current session.

---

### Task 8: Verification (project norms)

- [ ] **Step 1: Targeted pytest**

Run:

```bash
pytest tests/test_core_memory_tools.py tests/test_memory_service.py tests/test_rebuild_memory_fts.py -v
```

Expected: all **PASS**

- [ ] **Step 2: ruff**

Run:

```bash
ruff check minx_mcp tests scripts/rebuild_memory_fts.py
```

Expected: exit code **0**

- [ ] **Step 3: mypy**

Run:

```bash
mypy minx_mcp
```

Expected: exit code **0**

- [ ] **Step 4: Full pytest**

Run:

```bash
pytest
```

Expected: all **PASS**

- [ ] **Step 5: Whitespace / conflict marker check**

Run:

```bash
git diff --check
```

Expected: **no** trailing whitespace or conflict markers on staged/unstaged diffs

---

## Self-review (plan author)

**Spec coverage**

| Spec section | Task(s) |
|--------------|---------|
| Tool `memory_capture` + parameters | Task 4 |
| Render hints for Hermes (`response_template`, `response_slots`) | Tasks 3-4, 7 |
| `captured_thought`, not in `PAYLOAD_MODELS` | Tasks 4-5 |
| Payload shape `text`, `capture_type`, optional `metadata` | Tasks 3-4 |
| Reject `confidence >= 0.8` | Task 4 |
| `create_memory` reuse (scan, fingerprint, events) | Task 4 |
| Subject derivation + bounds | Task 3 |
| `capture_type` normalization | Task 3 |
| Metadata shape/depth/keys/string caps | Task 3 |
| FTS migration + rebuild (`$.text`, `$.capture_type` only) | Tasks 1-2 |
| No metadata in FTS | Task 3 note |
| Tests (three files) | Tasks 2-5 |
| README / HANDOFF | Task 7 |
| Vault readable / generic template | Task 6 |

**Placeholder scan:** No `TBD`/`TODO`/vague "add validation" steps; commands and test names are explicit.

**Type consistency:** Tool uses `float | int` for `confidence` like `memory_create`; `_coerce_confidence` shared; return shape `{"memory": ...}` matches `memory_create`.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-27-generic-memory-capture.md`. Two execution options:

**1. Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration. **Required sub-skill:** `superpowers:subagent-driven-development`.

**2. Inline Execution** - run tasks in one session with checkpoints. **Required sub-skill:** `superpowers:executing-plans`.

Which approach?

---

## Risks and notes

- **Metadata depth rule:** The spec's "max depth 4" must be implemented as one consistent tree-walk rule; mismatches between helper tests and tool behavior will show up in Task 3-4. Align tests with the exact recursion chosen.
- **FTS on existing databases:** New triggers cover new inserts/updates; **`rebuild_memory_fts.py` is mandatory** for backfill. HANDOFF must be explicit.
- **`test_memory_capture` positional args:** FastMCP may expose keyword-first ergonomics; if integration tests fail on argument order, switch tests to keyword calls (`cap(text="...", capture_type="observation", ...)`) rather than relying on position.
- **`capture_type` character set:** The spec is ASCII-centric. Implement `[a-z0-9_-]` unless product owners intentionally broaden it in a future spec.
