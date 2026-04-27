"""Queued memory embedding primitives.

The embedding provider is intentionally injectable. Core owns durable queueing,
secret/cost gates, and fallback search semantics; provider adapters can be added
without changing synchronous memory writes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from sqlite3 import Connection, IntegrityError

import httpx

from minx_mcp.contracts import ConflictError, InvalidInputError, LLMError, NotFoundError
from minx_mcp.core.enrichment_queue import EnrichmentHandler, EnrichmentJob, enqueue_enrichment_job
from minx_mcp.core.fingerprint import content_fingerprint
from minx_mcp.core.memory_models import MemoryRecord
from minx_mcp.core.memory_payloads import coerce_prior_payload_to_schema
from minx_mcp.core.memory_service import (
    MemorySearchResult,
    MemoryService,
    _memory_fingerprint_input,
    memory_record_as_dict,
)
from minx_mcp.core.secret_scanner import scan_for_secrets
from minx_mcp.validation import require_non_empty

EmbedFn = Callable[[str], tuple[list[float], int]]


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    max_cost_microusd_per_sweep: int
    api_key: str | None = None
    dimensions: int | None = None
    request_timeout_s: float = 30.0
    endpoint: str = "https://openrouter.ai/api/v1/embeddings"

    def __post_init__(self) -> None:
        require_non_empty("provider", self.provider)
        require_non_empty("model", self.model)
        if (
            not isinstance(self.max_cost_microusd_per_sweep, int)
            or isinstance(self.max_cost_microusd_per_sweep, bool)
            or self.max_cost_microusd_per_sweep < 1
        ):
            raise InvalidInputError("max_cost_microusd_per_sweep must be a positive integer")
        if self.dimensions is not None and (
            not isinstance(self.dimensions, int) or isinstance(self.dimensions, bool) or self.dimensions < 1
        ):
            raise InvalidInputError("dimensions must be a positive integer")
        if (
            not isinstance(self.request_timeout_s, (int, float))
            or isinstance(self.request_timeout_s, bool)
            or self.request_timeout_s <= 0
        ):
            raise InvalidInputError("request_timeout_s must be positive")
        require_non_empty("endpoint", self.endpoint)


class OpenRouterEmbedder:
    def __init__(self, config: EmbeddingConfig, *, transport: httpx.BaseTransport | None = None) -> None:
        if not config.api_key:
            raise InvalidInputError("OpenRouter API key is required for embeddings")
        self._config = config
        self._transport = transport

    def __call__(self, text: str) -> tuple[list[float], int]:
        _block_if_secret_text(text)
        payload: dict[str, object] = {
            "model": self._config.model,
            "input": text,
        }
        if self._config.dimensions is not None:
            payload["dimensions"] = self._config.dimensions
        try:
            with httpx.Client(
                timeout=self._config.request_timeout_s,
                transport=self._transport,
            ) as client:
                response = client.post(
                    self._config.endpoint,
                    headers={
                        "Authorization": f"Bearer {self._config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise LLMError(
                "Embedding provider request failed",
                data={"provider": self._config.provider, "model": self._config.model},
            ) from exc
        vector = _embedding_vector_from_response(body)
        _validate_vector(vector)
        return vector, _cost_microusd_from_response(body)


def embedding_config_from_settings(settings: object) -> EmbeddingConfig | None:
    api_key = getattr(settings, "openrouter_api_key", None)
    if not isinstance(api_key, str) or not api_key.strip():
        return None
    model = getattr(settings, "embedding_model", "openai/text-embedding-3-small")
    dimensions = getattr(settings, "embedding_dimensions", None)
    timeout = getattr(settings, "embedding_request_timeout_s", 30.0)
    max_cost = getattr(settings, "embedding_max_cost_microusd", 10_000)
    return EmbeddingConfig(
        provider="openrouter",
        model=str(model),
        max_cost_microusd_per_sweep=int(max_cost),
        api_key=api_key,
        dimensions=dimensions if isinstance(dimensions, int) else None,
        request_timeout_s=float(timeout),
    )


def openrouter_embedder(config: EmbeddingConfig) -> EmbedFn:
    return OpenRouterEmbedder(config)


def enqueue_memory_embedding(conn: Connection, memory_id: int) -> EnrichmentJob:
    mid = _validate_positive_int("memory_id", memory_id)
    row = conn.execute("SELECT id FROM memories WHERE id = ?", (mid,)).fetchone()
    if row is None:
        raise NotFoundError(f"Memory {mid} not found")
    return enqueue_enrichment_job(
        conn,
        job_type="memory.embedding",
        subject_type="memory",
        subject_id=mid,
        payload={"memory_id": mid},
    )


def memory_embedding_status(conn: Connection) -> dict[str, int]:
    embedded = int(conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0])
    queued = int(
        conn.execute(
            "SELECT COUNT(*) FROM enrichment_jobs WHERE job_type = 'memory.embedding' AND status = 'queued'"
        ).fetchone()[0]
    )
    dead = int(
        conn.execute(
            "SELECT COUNT(*) FROM enrichment_jobs WHERE job_type = 'memory.embedding' AND status = 'dead_letter'"
        ).fetchone()[0]
    )
    return {
        "embedded": embedded,
        "queued_jobs": queued,
        "dead_letter_jobs": dead,
    }


def memory_embedding_sweep_handlers(
    conn: Connection,
    *,
    config: EmbeddingConfig,
    embed: EmbedFn,
) -> dict[str, EnrichmentHandler]:
    return {"memory.embedding": lambda job: _process_memory_embedding_job(conn, job, config, embed)}


def _embedding_vector_from_response(body: object) -> list[float]:
    if not isinstance(body, dict):
        raise LLMError("Embedding provider response was not an object")
    data = body.get("data")
    if not isinstance(data, list) or not data:
        raise LLMError("Embedding provider response did not include embedding data")
    first = data[0]
    if not isinstance(first, dict):
        raise LLMError("Embedding provider response contained invalid embedding data")
    embedding = first.get("embedding")
    if not isinstance(embedding, list):
        raise LLMError("Embedding provider response did not include an embedding vector")
    return [float(value) for value in embedding]


def _cost_microusd_from_response(body: object) -> int:
    if not isinstance(body, dict):
        return 0
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return 0
    cost = usage.get("cost")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        return 0
    return round(float(cost) * 1_000_000)


def hybrid_memory_search(
    service: MemoryService,
    *,
    query: str,
    scope: str | None = None,
    memory_type: str | None = None,
    status: str | None = "active",
    limit: int = 25,
    embedding_config: EmbeddingConfig | None = None,
    embed: EmbedFn | None = None,
) -> dict[str, object]:
    results = service.search_memories(
        query=query,
        scope=scope,
        memory_type=memory_type,
        status=status,
        limit=limit,
    )
    if embedding_config is None or embed is None or not results:
        return _fts_fallback_payload(results)

    embedding_rows = _candidate_embeddings(
        service.conn,
        memory_ids=[result.memory.id for result in results],
        provider=embedding_config.provider,
        model=embedding_config.model,
    )
    if not embedding_rows:
        return _fts_fallback_payload(results)
    query_vector, cost = embed(query)
    _validate_vector(query_vector)
    if cost > embedding_config.max_cost_microusd_per_sweep:
        raise InvalidInputError("embedding cost exceeds configured sweep ceiling")
    ranked: list[tuple[float | None, int, MemorySearchResult]] = []
    for index, result in enumerate(results):
        vector = embedding_rows.get(result.memory.id)
        score = _cosine_similarity(query_vector, vector) if vector is not None else None
        ranked.append((score, index, result))
    if all(score is None for score, _index, _result in ranked):
        return _fts_fallback_payload(results)
    ranked.sort(key=lambda item: (item[0] is None, -(item[0] or 0.0), item[1]))
    return {
        "used_embeddings": True,
        "fallback": None,
        "provider": embedding_config.provider,
        "model": embedding_config.model,
        "results": [
            {
                "memory": memory_record_as_dict(result.memory),
                "rank": result.rank,
                "snippet": result.snippet,
                "semantic_score": score,
            }
            for score, _index, result in ranked
        ],
    }


def _fts_fallback_payload(results: list[MemorySearchResult]) -> dict[str, object]:
    return {
        "used_embeddings": False,
        "fallback": "fts5",
        "results": [
            {
                "memory": memory_record_as_dict(result.memory),
                "rank": result.rank,
                "snippet": result.snippet,
            }
            for result in results
        ],
    }


def _candidate_embeddings(
    conn: Connection,
    *,
    memory_ids: list[int],
    provider: str,
    model: str,
) -> dict[int, list[float]]:
    if not memory_ids:
        return {}
    placeholders = ",".join("?" for _ in memory_ids)
    rows = conn.execute(
        f"""
        SELECT memory_id, embedding_json, dimensions
        FROM memory_embeddings
        WHERE provider = ?
          AND model = ?
          AND memory_id IN ({placeholders})
        """,  # noqa: S608 - placeholders are generated, values are bound.
        [provider, model, *memory_ids],
    ).fetchall()
    out: dict[int, list[float]] = {}
    for row in rows:
        try:
            vector = json.loads(str(row["embedding_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(vector, list) or len(vector) != int(row["dimensions"]):
            continue
        try:
            parsed = [float(value) for value in vector]
        except (TypeError, ValueError):
            continue
        out[int(row["memory_id"])] = parsed
    return out


def _cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right):
        return None
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _process_memory_embedding_job(
    conn: Connection,
    job: EnrichmentJob,
    config: EmbeddingConfig,
    embed: EmbedFn,
) -> dict[str, object]:
    payload = _parse_payload(job.payload_json)
    raw_memory_id = payload.get("memory_id", job.subject_id)
    if not isinstance(raw_memory_id, int) or isinstance(raw_memory_id, bool):
        raise InvalidInputError("memory_id must be an integer")
    memory_id = _validate_positive_int("memory_id", raw_memory_id)
    service = MemoryService(Path(":memory:"), conn=conn)
    memory = service.get_memory(memory_id)
    fingerprint = ensure_memory_content_fingerprint(conn, memory)
    document = _memory_embedding_document(memory)
    _block_if_secret_text(document)
    vector, cost = embed(document)
    _validate_vector(vector)
    if cost > config.max_cost_microusd_per_sweep:
        raise InvalidInputError("embedding cost exceeds configured sweep ceiling")
    conn.execute(
        """
        INSERT INTO memory_embeddings (
            memory_id, content_fingerprint, provider, model, dimensions,
            embedding_json, cost_microusd, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(memory_id) DO UPDATE SET
            content_fingerprint = excluded.content_fingerprint,
            provider = excluded.provider,
            model = excluded.model,
            dimensions = excluded.dimensions,
            embedding_json = excluded.embedding_json,
            cost_microusd = excluded.cost_microusd,
            updated_at = datetime('now')
        """,
        (
            memory_id,
            fingerprint,
            config.provider,
            config.model,
            len(vector),
            json.dumps(vector),
            cost,
        ),
    )
    conn.commit()
    return {"memory_id": memory_id, "dimensions": len(vector), "cost_microusd": cost}


def ensure_memory_content_fingerprint(conn: Connection, memory: MemoryRecord) -> str:
    row = conn.execute(
        """
        SELECT memory_type, scope, subject, payload_json, content_fingerprint
        FROM memories
        WHERE id = ?
        """,
        (memory.id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"Memory {memory.id} not found")
    existing = row["content_fingerprint"]
    if existing is not None:
        return str(existing)
    fingerprint = _compute_memory_content_fingerprint(
        memory_type=str(row["memory_type"]),
        scope=str(row["scope"]),
        subject=str(row["subject"]),
        payload_json=str(row["payload_json"] or ""),
    )
    try:
        cur = conn.execute(
            """
            UPDATE memories
            SET content_fingerprint = ?,
                updated_at = datetime('now')
            WHERE id = ?
              AND content_fingerprint IS NULL
            """,
            (fingerprint, memory.id),
        )
    except IntegrityError as exc:
        raise ConflictError(
            "memory content_fingerprint collision prevents embedding",
            data={"memory_id": memory.id, "conflict_kind": "content_fingerprint_embedding"},
        ) from exc
    if cur.rowcount == 0:
        return _memory_content_fingerprint(conn, memory.id)
    conn.commit()
    return fingerprint


def _compute_memory_content_fingerprint(
    *,
    memory_type: str,
    scope: str,
    subject: str,
    payload_json: str,
) -> str:
    try:
        raw_payload = json.loads(payload_json) if payload_json else {}
        if not isinstance(raw_payload, dict):
            raw_payload = {}
        payload = coerce_prior_payload_to_schema(memory_type, raw_payload)
    except Exception:
        payload = {}
    try:
        parts = _memory_fingerprint_input(memory_type, payload, scope=scope, subject=subject)
    except Exception:
        parts = (memory_type, scope, subject, "", "")
    return content_fingerprint(*parts)


def _memory_embedding_document(memory: MemoryRecord) -> str:
    payload_bits = " ".join(str(value) for value in memory.payload.values())
    return " ".join(
        part
        for part in (
            memory.memory_type,
            memory.scope,
            memory.subject,
            payload_bits,
            memory.source,
            memory.reason,
        )
        if part
    )


def _memory_content_fingerprint(conn: Connection, memory_id: int) -> str:
    row = conn.execute("SELECT content_fingerprint FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Memory {memory_id} not found")
    value = row["content_fingerprint"]
    if value is None:
        raise InvalidInputError("memory content_fingerprint is required before embedding")
    return str(value)


def _parse_payload(raw: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInputError("embedding job payload_json is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidInputError("embedding job payload_json must be an object")
    return payload


def _block_if_secret_text(text: str) -> None:
    verdict = scan_for_secrets(text)
    if verdict.findings:
        raise InvalidInputError(
            "Secret detected in memory embedding input",
            data={
                "kind": "secret_detected",
                "surface": "memory_embedding",
                "detected_kinds": sorted({finding.kind for finding in verdict.findings}),
            },
        )


def _validate_vector(vector: list[float]) -> None:
    if not vector:
        raise InvalidInputError("embedding vector must not be empty")
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in vector):
        raise InvalidInputError("embedding vector values must be numbers")


def _validate_positive_int(field: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError(f"{field} must be an integer")
    if value < 1:
        raise InvalidInputError(f"{field} must be positive")
    return value
