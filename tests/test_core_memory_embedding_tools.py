from __future__ import annotations

import json
from pathlib import Path

import pytest

from minx_mcp.core import memory_embeddings
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from tests.helpers import MinxTestConfig, get_tool


def test_memory_embedding_enqueue_requires_configured_provider(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_memory = get_tool(server, "memory_create").fn
    enqueue = get_tool(server, "memory_embedding_enqueue").fn

    created = create_memory("preference", "core", "coffee", 0.95, {"value": "espresso"}, "user", "")
    result = enqueue(created["data"]["memory"]["id"])

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_memory_embedding_enqueue_and_status_tools_when_provider_configured(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault", openrouter_api_key="test-key"))
    create_memory = get_tool(server, "memory_create").fn
    enqueue = get_tool(server, "memory_embedding_enqueue").fn
    status = get_tool(server, "memory_embedding_status").fn

    created = create_memory("preference", "core", "coffee", 0.95, {"value": "espresso"}, "user", "")
    queued = enqueue(created["data"]["memory"]["id"])
    counts = status()

    assert queued["success"] is True
    assert queued["data"]["job"]["job_type"] == "memory.embedding"
    assert counts["success"] is True
    assert counts["data"]["status"]["queued_jobs"] == 1


def test_memory_hybrid_search_tool_falls_back_to_fts(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    create_memory = get_tool(server, "memory_create").fn
    search = get_tool(server, "memory_hybrid_search").fn

    created = create_memory(
        "preference",
        "core",
        "coffee",
        0.95,
        {"value": "espresso after training"},
        "user",
        "",
    )
    result = search("espresso", None, None, "active", 10)

    assert result["success"] is True
    assert result["data"]["used_embeddings"] is False
    assert result["data"]["fallback"] == "fts5"
    assert result["data"]["results"][0]["memory"]["id"] == created["data"]["memory"]["id"]


def test_memory_hybrid_search_tool_uses_embeddings_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    monkeypatch.setattr(memory_embeddings, "openrouter_embedder", lambda _config: (lambda _text: ([0.0, 1.0], 10)))
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault", openrouter_api_key="test-key"))
    create_memory = get_tool(server, "memory_create").fn
    search = get_tool(server, "memory_hybrid_search").fn

    first = create_memory(
        "preference",
        "core",
        "coffee",
        0.95,
        {"value": "espresso first"},
        "user",
        "",
    )["data"]["memory"]
    second = create_memory(
        "preference",
        "core",
        "coffee backup",
        0.95,
        {"value": "espresso second"},
        "user",
        "",
    )["data"]["memory"]
    conn = get_connection(db_path)
    try:
        for memory, vector in ((first, [1.0, 0.0]), (second, [0.0, 1.0])):
            row = conn.execute("SELECT content_fingerprint FROM memories WHERE id = ?", (memory["id"],)).fetchone()
            conn.execute(
                """
                INSERT INTO memory_embeddings (
                    memory_id, content_fingerprint, provider, model, dimensions,
                    embedding_json, cost_microusd
                ) VALUES (?, ?, 'openrouter', 'openai/text-embedding-3-small', 2, ?, 0)
                """,
                (memory["id"], row["content_fingerprint"], json.dumps(vector)),
            )
        conn.commit()
    finally:
        conn.close()

    result = search("espresso", None, None, "active", 10)

    assert result["success"] is True
    assert result["data"]["used_embeddings"] is True
    assert [item["memory"]["id"] for item in result["data"]["results"]] == [second["id"], first["id"]]
