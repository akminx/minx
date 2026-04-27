from __future__ import annotations

from minx_mcp.core.memory_service import MemoryService
from minx_mcp.db import get_connection
from scripts.rebuild_memory_fts import main


def _service_for(path):
    get_connection(path).close()
    return MemoryService(path)


def test_rebuild_memory_fts_empty_database_is_noop(tmp_path, capsys) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()

    assert main([str(db_path)]) == 0

    out = capsys.readouterr().out
    assert "Indexed 0 memory rows" in out


def test_rebuild_memory_fts_restores_missing_rows(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    svc = _service_for(db_path)
    record = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )
    svc.conn.execute("DELETE FROM memory_fts WHERE rowid = ?", (record.id,))
    svc.conn.commit()
    assert svc.search_memories(query="espresso") == []

    assert main([str(db_path)]) == 0

    assert [result.memory.id for result in svc.search_memories(query="espresso")] == [record.id]


def test_rebuild_memory_fts_indexes_entity_fact_aliases(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    svc = _service_for(db_path)
    record = svc.create_memory(
        memory_type="entity_fact",
        scope="finance",
        subject="market",
        confidence=0.95,
        payload={"category": "grocery", "aliases": ["Neighborhood Market", "Corner Shop"]},
        source="user",
        reason="manual",
    )
    svc.conn.execute("DELETE FROM memory_fts WHERE rowid = ?", (record.id,))
    svc.conn.commit()

    assert main([str(db_path)]) == 0

    assert [result.memory.id for result in svc.search_memories(query="corner")] == [record.id]


def test_rebuild_memory_fts_replaces_stale_rows(tmp_path) -> None:
    db_path = tmp_path / "m.db"
    svc = _service_for(db_path)
    record = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="drink",
        confidence=0.95,
        payload={"value": "tea"},
        source="user",
        reason="manual",
    )
    svc.conn.execute("UPDATE memory_fts SET payload_text = 'espresso' WHERE rowid = ?", (record.id,))
    svc.conn.commit()
    assert [result.memory.id for result in svc.search_memories(query="espresso")] == [record.id]

    assert main([str(db_path)]) == 0

    assert svc.search_memories(query="espresso") == []
    assert [result.memory.id for result in svc.search_memories(query="tea")] == [record.id]


def test_rebuild_memory_fts_is_idempotent(tmp_path, capsys) -> None:
    db_path = tmp_path / "m.db"
    svc = _service_for(db_path)
    svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )

    assert main([str(db_path)]) == 0
    assert main([str(db_path)]) == 0

    out = capsys.readouterr().out
    assert out.count("Indexed 1 memory rows") == 2
    assert len(svc.search_memories(query="espresso")) == 1
