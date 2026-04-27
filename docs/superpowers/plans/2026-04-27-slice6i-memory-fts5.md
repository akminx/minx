# Slice 6i Memory FTS5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic FTS5 search over durable memories and expose it through `memory_search`.

**Architecture:** SQLite owns the offline full-text index. `MemoryService` owns search semantics and result serialization helpers. The MCP tool is a thin wrapper that validates tool inputs and returns the existing `ToolResponse` envelope.

**Tech Stack:** Python 3.12, SQLite FTS5, FastMCP, pytest, mypy, ruff.

---

## File Structure

- Create `minx_mcp/schema/migrations/021_memory_fts5.sql` for the FTS table and triggers.
- Modify `minx_mcp/core/memory_service.py` for `MemorySearchResult`, search document flattening, and `search_memories`.
- Modify `minx_mcp/core/tools/memory.py` to register `memory_search`.
- Create `scripts/rebuild_memory_fts.py` for one-shot repair/backfill.
- Modify `tests/test_memory_service.py` for service-level search behavior.
- Modify `tests/test_core_memory_tools.py` for the MCP wrapper.
- Create `tests/test_rebuild_memory_fts.py` for the operator script.
- Update `HANDOFF.md` after implementation with migration and verification notes.

## Task 1: Migration

- **Step 1: Write failing migration tests**

Add tests in `tests/test_db.py`:

```python
def test_latest_schema_includes_memory_fts(db_path: Path) -> None:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master WHERE name IN "
            "('memory_fts', 'memories_ai_fts', 'memories_au_fts', 'memories_ad_fts')"
        ).fetchall()
    finally:
        conn.close()

    assert {(row["name"], row["type"]) for row in rows} == {
        ("memory_fts", "table"),
        ("memories_ai_fts", "trigger"),
        ("memories_au_fts", "trigger"),
        ("memories_ad_fts", "trigger"),
    }
```

- **Step 2: Run the migration test**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_latest_schema_includes_memory_fts -q`

Expected: fails because `memory_fts` does not exist.

- **Step 3: Add `021_memory_fts5.sql`**

Create `minx_mcp/schema/migrations/021_memory_fts5.sql` with:

```sql
-- Slice 6i: deterministic full-text search over memories.

CREATE VIRTUAL TABLE memory_fts USING fts5(
    memory_type,
    scope,
    subject,
    payload_text,
    source,
    reason,
    tokenize = 'unicode61'
);

CREATE TRIGGER memories_ai_fts AFTER INSERT ON memories BEGIN
    INSERT INTO memory_fts(rowid, memory_type, scope, subject, payload_text, source, reason)
    VALUES (
        new.id,
        new.memory_type,
        new.scope,
        new.subject,
        COALESCE(json_extract(new.payload_json, '$.value'), '') || ' ' ||
        COALESCE(json_extract(new.payload_json, '$.note'), '') || ' ' ||
        COALESCE(json_extract(new.payload_json, '$.signal'), '') || ' ' ||
        COALESCE(json_extract(new.payload_json, '$.limit_value'), ''),
        new.source,
        new.reason
    );
END;

CREATE TRIGGER memories_au_fts AFTER UPDATE OF memory_type, scope, subject, payload_json, source, reason ON memories BEGIN
    DELETE FROM memory_fts WHERE rowid = old.id;
    INSERT INTO memory_fts(rowid, memory_type, scope, subject, payload_text, source, reason)
    VALUES (
        new.id,
        new.memory_type,
        new.scope,
        new.subject,
        COALESCE(json_extract(new.payload_json, '$.value'), '') || ' ' ||
        COALESCE(json_extract(new.payload_json, '$.note'), '') || ' ' ||
        COALESCE(json_extract(new.payload_json, '$.signal'), '') || ' ' ||
        COALESCE(json_extract(new.payload_json, '$.limit_value'), ''),
        new.source,
        new.reason
    );
END;

CREATE TRIGGER memories_ad_fts AFTER DELETE ON memories BEGIN
    DELETE FROM memory_fts WHERE rowid = old.id;
END;
```

- **Step 4: Run the migration test again**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_latest_schema_includes_memory_fts -q`

Expected: pass.

## Task 2: Service Search API

- **Step 1: Write failing service tests**

Add tests in `tests/test_memory_service.py`:

```python
def test_search_memories_finds_active_memory_by_payload_value(tmp_path: Path) -> None:
    svc = _fresh_memory_service(tmp_path)
    record = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "prefers espresso after training"},
        source="user",
        reason="manual",
    )

    results = svc.search_memories(query="espresso", limit=10)

    assert [result.memory.id for result in results] == [record.id]
    assert "espresso" in results[0].snippet.lower()
```

Add these tests in the same file:

```python
def test_search_memories_updates_index_when_payload_changes(tmp_path: Path) -> None:
    svc = _fresh_memory_service(tmp_path)
    record = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="drink",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )

    svc.update_payload(record.id, {"value": "tea"}, actor="user", reason="changed")

    assert svc.search_memories(query="espresso") == []
    assert [result.memory.id for result in svc.search_memories(query="tea")] == [record.id]


def test_search_memories_defaults_to_active_status(tmp_path: Path) -> None:
    svc = _fresh_memory_service(tmp_path)
    active = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="active",
        confidence=0.95,
        payload={"value": "matchable"},
        source="user",
        reason="manual",
    )
    rejected = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="rejected",
        confidence=0.4,
        payload={"value": "matchable"},
        source="user",
        reason="manual",
    )
    svc.reject_memory(rejected.id, actor="user", reason="no")

    assert [result.memory.id for result in svc.search_memories(query="matchable")] == [active.id]
    assert {result.memory.id for result in svc.search_memories(query="matchable", status=None)} == {
        active.id,
        rejected.id,
    }


def test_search_memories_scope_and_type_filters(tmp_path: Path) -> None:
    svc = _fresh_memory_service(tmp_path)
    keep = svc.create_memory(
        memory_type="preference",
        scope="finance",
        subject="merchant",
        confidence=0.95,
        payload={"value": "coffee"},
        source="user",
        reason="manual",
    )
    svc.create_memory(
        memory_type="constraint",
        scope="finance",
        subject="budget",
        confidence=0.95,
        payload={"limit_value": "coffee"},
        source="user",
        reason="manual",
    )

    results = svc.search_memories(query="coffee", scope="finance", memory_type="preference")

    assert [result.memory.id for result in results] == [keep.id]


def test_search_memories_rejects_invalid_query_and_limit(tmp_path: Path) -> None:
    svc = _fresh_memory_service(tmp_path)

    with pytest.raises(InvalidInputError):
        svc.search_memories(query='"unterminated')
    with pytest.raises(InvalidInputError):
        svc.search_memories(query="coffee", limit=0)
```

- **Step 2: Run service tests**

Run: `.venv/bin/python -m pytest tests/test_memory_service.py -k 'search_memories' -q`

Expected: fail because `search_memories` is undefined.

- **Step 3: Implement service types and query**

In `minx_mcp/core/memory_service.py`, add:

```python
@dataclass(frozen=True)
class MemorySearchResult:
    memory: MemoryRecord
    rank: float
    snippet: str
```

Add `search_memories(...)` that:

- validates query and limit,
- builds a parameterized SQL query using `memory_fts MATCH ?`,
- joins `memory_fts` to `memories` by `memory_fts.rowid = memories.id`,
- applies optional status/scope/type filters,
- catches `sqlite3.OperationalError` for FTS syntax failures and raises `InvalidInputError("query is not valid FTS5 syntax")`,
- hydrates each memory with the existing row-to-record helper.
- **Step 4: Run service tests again**

Run: `.venv/bin/python -m pytest tests/test_memory_service.py -k 'search_memories' -q`

Expected: pass.

## Task 3: MCP Tool

- **Step 1: Write failing MCP tests**

Add tests in `tests/test_core_memory_tools.py` for:

- `memory_search(query="espresso")` returns `{"results": [...]}`.
- blank query returns an `INVALID_INPUT` envelope.
- invalid limit returns an `INVALID_INPUT` envelope.
- **Step 2: Run MCP tests**

Run: `.venv/bin/python -m pytest tests/test_core_memory_tools.py -k 'memory_search' -q`

Expected: fail because tool is not registered.

- **Step 3: Register `memory_search`**

In `minx_mcp/core/tools/memory.py`:

- add `@mcp.tool(name="memory_search")`,
- normalize optional filters with `_normalize_optional_filter`,
- use `coerce_limit(limit, maximum=100)`,
- call `MemoryService.search_memories`,
- return:

```python
{
    "results": [
        {
            "memory": memory_record_as_dict(result.memory),
            "rank": result.rank,
            "snippet": result.snippet,
        }
        for result in results
    ]
}
```

- **Step 4: Run MCP tests again**

Run: `.venv/bin/python -m pytest tests/test_core_memory_tools.py -k 'memory_search' -q`

Expected: pass.

## Task 4: Rebuild Script

- **Step 1: Write failing script tests**

Create `tests/test_rebuild_memory_fts.py` covering:

- empty database is a no-op,
- deleted FTS rows are restored,
- stale FTS rows are replaced after payload changes,
- running twice is idempotent.
- **Step 2: Implement script**

Create `scripts/rebuild_memory_fts.py` with a `main(argv: list[str] | None = None) -> int` entrypoint. It should:

- accept optional DB path, defaulting to `settings.db_path`,
- open a connection,
- run `DELETE FROM memory_fts`,
- repopulate from `memories`,
- print a count of indexed rows,
- return `0`.
- **Step 3: Run script tests**

Run: `.venv/bin/python -m pytest tests/test_rebuild_memory_fts.py -q`

Expected: pass.

## Task 5: Verification and Handoff

- **Step 1: Run targeted tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_db.py \
  tests/test_memory_service.py -k 'search_memories' \
  tests/test_core_memory_tools.py -k 'memory_search' \
  tests/test_rebuild_memory_fts.py -q
```

- **Step 2: Run quality gates**

Run:

```bash
.venv/bin/python -m ruff check minx_mcp tests scripts
.venv/bin/python -m mypy minx_mcp
.venv/bin/python -m pytest tests/ -x -q
```

- **Step 3: Update handoff**

Update `HANDOFF.md` with:

- Slice 6i implemented date,
- migration `021_memory_fts5.sql`,
- new MCP tool `memory_search`,
- operator step `python -m scripts.rebuild_memory_fts`,
- verification commands and outcomes.

## Self-Review

- Spec coverage: schema, service, MCP tool, script, and tests are covered.
- No external dependency: this stays SQLite-only.
- Security: this relies on the 6h write gate and does not add outbound calls.
- Migration order: 6i claims `021`; Slice 9 moves later.

