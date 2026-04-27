from __future__ import annotations

from pathlib import Path

import pytest

from minx_mcp.core import memory_embeddings
from minx_mcp.core.enrichment_queue import enqueue_enrichment_job
from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from tests.helpers import MinxTestConfig, get_tool


def test_enrichment_status_and_sweep_tools_process_configured_embedding_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT INTO memories (
            memory_type, scope, subject, confidence, status, payload_json,
            source, reason, content_fingerprint
        ) VALUES ('preference', 'core', 'coffee', 0.95, 'active', '{"value": "espresso"}', 'user', '', 'fp')
        """
    )
    memory_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    enqueue_enrichment_job(
        conn,
        job_type="memory.embedding",
        subject_type="memory",
        subject_id=memory_id,
        payload={"memory_id": memory_id},
        max_attempts=1,
    )
    conn.close()
    config = MinxTestConfig(db_path, tmp_path / "vault", openrouter_api_key="test-key")
    monkeypatch.setattr(memory_embeddings, "openrouter_embedder", lambda _config: (lambda _text: ([0.1, 0.2], 10)))
    server = create_core_server(config)
    status = get_tool(server, "enrichment_status").fn
    sweep = get_tool(server, "enrichment_sweep").fn

    before = status()
    swept = sweep(10)
    after = status()

    assert before["success"] is True
    assert before["data"]["counts"]["queued"] == 1
    assert swept["success"] is True
    assert swept["data"]["report"]["claimed"] == 1
    assert swept["data"]["report"]["succeeded"] == 1
    assert after["data"]["counts"]["succeeded"] == 1


def test_enrichment_retry_dead_letter_tool(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    conn = get_connection(db_path)
    job = enqueue_enrichment_job(
        conn,
        job_type="memory.embedding",
        subject_type="memory",
        subject_id=42,
        payload={"memory_id": 42},
        max_attempts=1,
    )
    conn.close()
    server = create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))
    sweep = get_tool(server, "enrichment_sweep").fn
    retry = get_tool(server, "enrichment_retry_dead_letter").fn

    sweep(10)
    retried = retry(job.id)

    assert retried["success"] is True
    assert retried["data"]["job"]["status"] == "queued"
    assert retried["data"]["job"]["attempts"] == 0
