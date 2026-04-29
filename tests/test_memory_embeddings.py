from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.enrichment_queue import sweep_enrichment_queue
from minx_mcp.core.fingerprint import content_fingerprint
from minx_mcp.core.memory_embeddings import (
    EmbeddingConfig,
    OpenRouterEmbedder,
    enqueue_memory_embedding,
    hybrid_memory_search,
    memory_embedding_status,
    memory_embedding_sweep_handlers,
)
from minx_mcp.core.memory_service import MemoryService, _memory_fingerprint_input
from minx_mcp.db import get_connection


def _service(tmp_path) -> MemoryService:
    db_path = tmp_path / "m.db"
    get_connection(db_path).close()
    return MemoryService(db_path)


def test_enqueue_memory_embedding_creates_queue_job(tmp_path) -> None:
    svc = _service(tmp_path)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )

    job = enqueue_memory_embedding(svc.conn, memory.id)

    assert job.job_type == "memory.embedding"
    assert job.subject_type == "memory"
    assert job.subject_id == memory.id
    assert json.loads(job.payload_json) == {"memory_id": memory.id}


def test_enqueue_memory_embedding_rejects_non_active_memory(tmp_path) -> None:
    svc = _service(tmp_path)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.4,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )

    with pytest.raises(InvalidInputError):
        enqueue_memory_embedding(svc.conn, memory.id)


def test_embedding_job_skips_memory_rejected_after_enqueue(tmp_path) -> None:
    svc = _service(tmp_path)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )
    enqueue_memory_embedding(svc.conn, memory.id)
    svc.expire_memory(memory.id, actor="user", reason="stale")
    called = False

    def embed(_text: str) -> tuple[list[float], int]:
        nonlocal called
        called = True
        return [0.1], 10

    report = sweep_enrichment_queue(
        svc.conn,
        limit=10,
        handlers=memory_embedding_sweep_handlers(
            svc.conn,
            config=EmbeddingConfig(provider="test", model="fake-embedding", max_cost_microusd_per_sweep=100),
            embed=embed,
        ),
    )

    assert report.failed == 1
    assert called is False
    assert svc.conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0


def test_memory_embedding_handler_rejects_non_finite_vector(tmp_path) -> None:
    svc = _service(tmp_path)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )
    enqueue_memory_embedding(svc.conn, memory.id)

    def embed(_text: str) -> tuple[list[float], int]:
        return [float("nan")], 10

    report = sweep_enrichment_queue(
        svc.conn,
        limit=10,
        handlers=memory_embedding_sweep_handlers(
            svc.conn,
            config=EmbeddingConfig(provider="test", model="fake-embedding", max_cost_microusd_per_sweep=100),
            embed=embed,
        ),
    )

    assert report.failed == 1
    assert svc.conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 0


def test_memory_embedding_handler_writes_embedding_with_configured_cost(tmp_path) -> None:
    svc = _service(tmp_path)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )
    enqueue_memory_embedding(svc.conn, memory.id)

    def embed(text: str) -> tuple[list[float], int]:
        assert "espresso" in text
        return [0.1, 0.2, 0.3], 25

    report = sweep_enrichment_queue(
        svc.conn,
        limit=10,
        handlers=memory_embedding_sweep_handlers(
            svc.conn,
            config=EmbeddingConfig(provider="test", model="fake-embedding", max_cost_microusd_per_sweep=100),
            embed=embed,
        ),
    )

    assert report.succeeded == 1
    row = svc.conn.execute("SELECT * FROM memory_embeddings WHERE memory_id = ?", (memory.id,)).fetchone()
    assert row["provider"] == "test"
    assert row["model"] == "fake-embedding"
    assert row["dimensions"] == 3
    assert json.loads(row["embedding_json"]) == [0.1, 0.2, 0.3]


def test_memory_embedding_handler_rejects_cost_over_ceiling(tmp_path) -> None:
    svc = _service(tmp_path)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )
    enqueue_memory_embedding(svc.conn, memory.id)

    def embed(_text: str) -> tuple[list[float], int]:
        return [0.1], 101

    report = sweep_enrichment_queue(
        svc.conn,
        limit=10,
        handlers=memory_embedding_sweep_handlers(
            svc.conn,
            config=EmbeddingConfig(provider="test", model="fake-embedding", max_cost_microusd_per_sweep=100),
            embed=embed,
        ),
    )

    assert report.failed == 1
    assert memory_embedding_status(svc.conn)["queued_jobs"] == 1


def test_memory_embedding_handler_fingerprints_legacy_row_before_provider_call(tmp_path) -> None:
    svc = _service(tmp_path)
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status, payload_json,
            source, reason, content_fingerprint
        ) VALUES ('preference', 'core', 'legacy', 0.95, ?, ?, 'legacy', '', NULL)
        """,
        ("active", json.dumps({"value": "espresso"})),
    )
    memory_id = int(svc.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    svc.conn.commit()
    enqueue_memory_embedding(svc.conn, memory_id)
    called = False

    def embed(_text: str) -> tuple[list[float], int]:
        nonlocal called
        called = True
        return [0.1], 10

    report = sweep_enrichment_queue(
        svc.conn,
        limit=10,
        handlers=memory_embedding_sweep_handlers(
            svc.conn,
            config=EmbeddingConfig(provider="test", model="fake-embedding", max_cost_microusd_per_sweep=100),
            embed=embed,
        ),
    )

    assert report.succeeded == 1
    assert called is True
    row = svc.conn.execute(
        "SELECT content_fingerprint FROM memories WHERE id = ?",
        (memory_id,),
    ).fetchone()
    assert row["content_fingerprint"] is not None


def test_memory_embedding_handler_does_not_call_provider_when_fingerprint_collides(tmp_path) -> None:
    svc = _service(tmp_path)
    first = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="first",
        confidence=0.95,
        payload={"value": "espresso"},
        source="user",
        reason="manual",
    )
    colliding_fp = content_fingerprint(
        *_memory_fingerprint_input(
            "preference",
            {"value": "espresso"},
            scope="core",
            subject="second",
        )
    )
    svc.conn.execute("UPDATE memories SET content_fingerprint = ? WHERE id = ?", (colliding_fp, first.id))
    svc.conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status, payload_json,
            source, reason, content_fingerprint
        ) VALUES ('preference', 'core', 'second', 0.95, ?, ?, 'legacy', '', NULL)
        """,
        ("active", json.dumps({"value": "espresso"})),
    )
    second_id = int(svc.conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    svc.conn.commit()
    enqueue_memory_embedding(svc.conn, second_id)
    called = False

    def embed(_text: str) -> tuple[list[float], int]:
        nonlocal called
        called = True
        return [0.1], 10

    report = sweep_enrichment_queue(
        svc.conn,
        limit=10,
        handlers=memory_embedding_sweep_handlers(
            svc.conn,
            config=EmbeddingConfig(provider="test", model="fake-embedding", max_cost_microusd_per_sweep=100),
            embed=embed,
        ),
    )

    assert first.id != second_id
    assert report.failed == 1
    assert called is False


def test_hybrid_memory_search_uses_fts_fallback_without_embeddings(tmp_path) -> None:
    svc = _service(tmp_path)
    memory = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso after training"},
        source="user",
        reason="manual",
    )

    results = hybrid_memory_search(svc, query="espresso", limit=10)

    assert [result["memory"]["id"] for result in results["results"]] == [memory.id]
    assert results["used_embeddings"] is False
    assert results["fallback"] == "fts5"


def test_hybrid_memory_search_reranks_with_matching_embeddings(tmp_path) -> None:
    svc = _service(tmp_path)
    first = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee",
        confidence=0.95,
        payload={"value": "espresso after training"},
        source="user",
        reason="manual",
    )
    second = svc.create_memory(
        memory_type="preference",
        scope="core",
        subject="coffee backup",
        confidence=0.95,
        payload={"value": "espresso after dinner"},
        source="user",
        reason="manual",
    )
    svc.conn.execute(
        """
        INSERT INTO memory_embeddings (
            memory_id, content_fingerprint, provider, model, dimensions,
            embedding_json, cost_microusd
        ) VALUES (?, ?, 'test', 'fake-embedding', 2, ?, 0)
        """,
        (first.id, _fingerprint(svc.conn, first.id), json.dumps([1.0, 0.0])),
    )
    svc.conn.execute(
        """
        INSERT INTO memory_embeddings (
            memory_id, content_fingerprint, provider, model, dimensions,
            embedding_json, cost_microusd
        ) VALUES (?, ?, 'test', 'fake-embedding', 2, ?, 0)
        """,
        (second.id, _fingerprint(svc.conn, second.id), json.dumps([0.0, 1.0])),
    )
    svc.conn.commit()

    results = hybrid_memory_search(
        svc,
        query="espresso",
        limit=10,
        embedding_config=EmbeddingConfig(provider="test", model="fake-embedding", max_cost_microusd_per_sweep=100),
        embed=lambda _query: ([0.0, 1.0], 10),
    )

    assert results["used_embeddings"] is True
    assert results["provider"] == "test"
    assert results["model"] == "fake-embedding"
    assert [result["memory"]["id"] for result in results["results"]] == [second.id, first.id]
    assert results["results"][0]["semantic_score"] == pytest.approx(1.0)


def test_openrouter_embedder_parses_embedding_and_cost_without_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://openrouter.ai/api/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload == {
            "input": "hello",
            "model": "openai/text-embedding-3-small",
            "dimensions": 2,
        }
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [0.25, 0.75], "object": "embedding", "index": 0}],
                "model": "openai/text-embedding-3-small",
                "object": "list",
                "usage": {"cost": 0.000123, "prompt_tokens": 1, "total_tokens": 1},
            },
        )

    embedder = OpenRouterEmbedder(
        EmbeddingConfig(
            provider="openrouter",
            model="openai/text-embedding-3-small",
            max_cost_microusd_per_sweep=1_000,
            api_key="test-key",
            dimensions=2,
        ),
        transport=httpx.MockTransport(handler),
    )

    assert embedder("hello") == ([0.25, 0.75], 123)


def test_embedding_config_requires_provider_model_and_cost_ceiling() -> None:
    with pytest.raises(InvalidInputError):
        EmbeddingConfig(provider="", model="m", max_cost_microusd_per_sweep=100)
    with pytest.raises(InvalidInputError):
        EmbeddingConfig(provider="p", model="", max_cost_microusd_per_sweep=100)
    with pytest.raises(InvalidInputError):
        EmbeddingConfig(provider="p", model="m", max_cost_microusd_per_sweep=0)


def _fingerprint(conn: sqlite3.Connection, memory_id: int) -> str:
    row = conn.execute("SELECT content_fingerprint FROM memories WHERE id = ?", (memory_id,)).fetchone()
    assert row is not None
    value = row["content_fingerprint"]
    assert value is not None
    return str(value)
