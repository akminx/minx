"""Memory MCP tools: list / get / create / capture / confirm / reject / expire / candidates."""

from __future__ import annotations

import math
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import InvalidInputError, ToolResponse, wrap_tool_call
from minx_mcp.core import memory_embeddings
from minx_mcp.core.enrichment_queue import EnrichmentJob
from minx_mcp.core.memory_capture import (
    build_capture_response_slots,
    build_captured_thought_payload,
    derive_capture_subject,
    normalize_capture_text_for_body,
    normalize_capture_type,
    validate_capture_metadata,
)
from minx_mcp.core.memory_embeddings import (
    enqueue_memory_embedding,
    hybrid_memory_search,
    memory_embedding_status,
)
from minx_mcp.core.memory_service import (
    ACTIVE_CONFIDENCE_THRESHOLD,
    MemoryService,
    memory_edge_as_dict,
    memory_record_as_dict,
)
from minx_mcp.core.tools._shared import CoreServiceConfig, coerce_limit
from minx_mcp.db import scoped_connection
from minx_mcp.validation import require_non_empty, require_payload_object

__all__ = ["register_memory_tools"]


def register_memory_tools(mcp: FastMCP, config: CoreServiceConfig) -> None:
    @mcp.tool(name="memory_list")
    def memory_list_tool(
        status: str | None = None,
        memory_type: str | None = None,
        scope: str | None = None,
        limit: int = 100,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_list(config, status, memory_type, scope, limit),
            tool_name="memory_list",
        )

    @mcp.tool(name="memory_get")
    def memory_get_tool(memory_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_get(config, memory_id),
            tool_name="memory_get",
        )

    @mcp.tool(name="memory_create")
    def memory_create_tool(
        memory_type: str,
        scope: str,
        subject: str,
        confidence: float | int,
        payload: object,
        source: str,
        reason: str = "",
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_create(
                config,
                memory_type,
                scope,
                subject,
                confidence,
                payload,
                source,
                reason,
            ),
            tool_name="memory_create",
        )

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
        """Quick-capture text as a candidate memory for later review.

        Defaults to capture_type="observation", scope="core", source="user:capture",
        and confidence=0.5. Captures must stay below confidence 0.8 and remain
        candidate rows until memory_confirm. memory_search defaults to active rows,
        so reviewers should pass status="candidate" or status=None to find captures.
        Duplicate live captures can return CONFLICT through normal memory dedupe rules.
        Harnesses should render acknowledgement copy from response_template/slots.
        """
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

    @mcp.tool(name="memory_confirm")
    def memory_confirm_tool(memory_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_confirm(config, memory_id),
            tool_name="memory_confirm",
        )

    @mcp.tool(name="memory_reject")
    def memory_reject_tool(memory_id: int, reason: str = "") -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_reject(config, memory_id, reason),
            tool_name="memory_reject",
        )

    @mcp.tool(name="memory_expire")
    def memory_expire_tool(
        memory_id: int,
        reason: str = "",
        actor: str = "system",
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_expire(config, memory_id, reason, actor),
            tool_name="memory_expire",
        )

    @mcp.tool(name="memory_search")
    def memory_search_tool(
        query: str,
        scope: str | None = None,
        memory_type: str | None = None,
        status: str | None = "active",
        limit: int = 25,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_search(config, query, scope, memory_type, status, limit),
            tool_name="memory_search",
        )

    @mcp.tool(name="memory_hybrid_search")
    def memory_hybrid_search_tool(
        query: str,
        scope: str | None = None,
        memory_type: str | None = None,
        status: str | None = "active",
        limit: int = 25,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_hybrid_search(config, query, scope, memory_type, status, limit),
            tool_name="memory_hybrid_search",
        )

    @mcp.tool(name="memory_embedding_enqueue")
    def memory_embedding_enqueue_tool(memory_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_embedding_enqueue(config, memory_id),
            tool_name="memory_embedding_enqueue",
        )

    @mcp.tool(name="memory_embedding_status")
    def memory_embedding_status_tool() -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_embedding_status(config),
            tool_name="memory_embedding_status",
        )

    @mcp.tool(name="memory_edge_create")
    def memory_edge_create_tool(
        source_memory_id: int,
        target_memory_id: int,
        predicate: str,
        relation_note: str = "",
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_edge_create(
                config,
                source_memory_id,
                target_memory_id,
                predicate,
                relation_note,
            ),
            tool_name="memory_edge_create",
        )

    @mcp.tool(name="memory_edge_list")
    def memory_edge_list_tool(
        memory_id: int,
        direction: str = "both",
        predicate: str | None = None,
        limit: int = 100,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_edge_list(config, memory_id, direction, predicate, limit),
            tool_name="memory_edge_list",
        )

    @mcp.tool(name="memory_edge_delete")
    def memory_edge_delete_tool(edge_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _memory_edge_delete(config, edge_id),
            tool_name="memory_edge_delete",
        )

    @mcp.tool(name="get_pending_memory_candidates")
    def get_pending_memory_candidates_tool(
        scope: str | None = None,
        limit: int = 50,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _get_pending_memory_candidates(config, scope, limit),
            tool_name="get_pending_memory_candidates",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _memory_list(
    config: CoreServiceConfig,
    status: str | None,
    memory_type: str | None,
    scope: str | None,
    limit: int,
) -> dict[str, object]:
    effective_status = _normalize_optional_filter(status)
    effective_type = _normalize_optional_filter(memory_type)
    effective_scope = _normalize_optional_filter(scope)
    lim = coerce_limit(limit, maximum=500)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        service.prune_expired_memories()
        conn.commit()
        if effective_status == "active":
            rows = service.list_active_memories(
                memory_type=effective_type,
                scope=effective_scope,
                limit=lim,
            )
        else:
            rows = service.list_memories(
                status=effective_status,
                memory_type=effective_type,
                scope=effective_scope,
                limit=lim,
            )
        return {"memories": [memory_record_as_dict(r) for r in rows]}


def _memory_get(config: CoreServiceConfig, memory_id: int) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.get_memory(mid)
        return {"memory": memory_record_as_dict(record)}


def _memory_create(
    config: CoreServiceConfig,
    memory_type: str,
    scope: str,
    subject: str,
    confidence: float | int,
    payload: object,
    source: str,
    reason: str,
) -> dict[str, object]:
    mt = require_non_empty("memory_type", memory_type)
    sc = require_non_empty("scope", scope)
    sj = require_non_empty("subject", subject)
    src = require_non_empty("source", source)
    body = require_payload_object(payload, field_name="payload")
    conf = _coerce_confidence(confidence)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.create_memory(
            memory_type=mt,
            scope=sc,
            subject=sj,
            confidence=conf,
            payload=body,
            source=src,
            reason=reason,
            actor="user",
        )
        return {"memory": memory_record_as_dict(record)}


def _memory_capture(
    config: CoreServiceConfig,
    text: str,
    capture_type: str,
    scope: str,
    subject: str | None,
    source: str,
    confidence: float | int,
    metadata: object | None,
) -> dict[str, object]:
    normalized_text = normalize_capture_text_for_body(text)
    if not normalized_text:
        raise InvalidInputError("text must be non-empty")
    capture_type_normalized = normalize_capture_type(capture_type)
    metadata_payload = validate_capture_metadata(metadata)
    conf = _coerce_confidence(confidence)
    if conf >= ACTIVE_CONFIDENCE_THRESHOLD:
        raise InvalidInputError(
            f"confidence must be below {ACTIVE_CONFIDENCE_THRESHOLD} for memory_capture; "
            "use memory_create for active memories"
        )
    sc = require_non_empty("scope", scope)
    src = require_non_empty("source", source)
    sj = derive_capture_subject(
        capture_type_normalized=capture_type_normalized,
        raw_text=text,
        explicit_subject=subject,
    )
    payload = build_captured_thought_payload(
        text=normalized_text,
        capture_type=capture_type_normalized,
        metadata=metadata_payload,
    )
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.create_memory(
            memory_type="captured_thought",
            scope=sc,
            subject=sj,
            confidence=conf,
            payload=payload,
            source=src,
            reason="",
            actor="user",
        )
        return {
            "memory": memory_record_as_dict(record),
            "response_template": "memory_capture.created_candidate",
            "response_slots": build_capture_response_slots(
                record=record,
                capture_type=capture_type_normalized,
            ),
        }


def _memory_confirm(config: CoreServiceConfig, memory_id: int) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.confirm_memory(mid, actor="user")
        return {"memory": memory_record_as_dict(record)}


def _memory_reject(config: CoreServiceConfig, memory_id: int, reason: str) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.reject_memory(mid, actor="user", reason=reason)
        return {"memory": memory_record_as_dict(record)}


def _memory_expire(
    config: CoreServiceConfig,
    memory_id: int,
    reason: str,
    actor: str = "system",
) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        record = service.expire_memory(mid, actor=actor, reason=reason)
        return {"memory": memory_record_as_dict(record)}


def _memory_search(
    config: CoreServiceConfig,
    query: str,
    scope: str | None,
    memory_type: str | None,
    status: str | None,
    limit: int,
) -> dict[str, object]:
    effective_scope = _normalize_optional_filter(scope)
    effective_type = _normalize_optional_filter(memory_type)
    effective_status = _normalize_optional_filter(status)
    lim = coerce_limit(limit, maximum=100)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        results = service.search_memories(
            query=query,
            scope=effective_scope,
            memory_type=effective_type,
            status=effective_status,
            limit=lim,
        )
        return {
            "results": [
                {
                    "memory": memory_record_as_dict(result.memory),
                    "rank": result.rank,
                    "snippet": result.snippet,
                }
                for result in results
            ]
        }


def _memory_hybrid_search(
    config: CoreServiceConfig,
    query: str,
    scope: str | None,
    memory_type: str | None,
    status: str | None,
    limit: int,
) -> dict[str, object]:
    effective_scope = _normalize_optional_filter(scope)
    effective_type = _normalize_optional_filter(memory_type)
    effective_status = _normalize_optional_filter(status)
    lim = coerce_limit(limit, maximum=100)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        embedding_config = memory_embeddings.embedding_config_from_settings(config)
        return hybrid_memory_search(
            service,
            query=query,
            scope=effective_scope,
            memory_type=effective_type,
            status=effective_status,
            limit=lim,
            embedding_config=embedding_config,
            embed=memory_embeddings.openrouter_embedder(embedding_config) if embedding_config is not None else None,
        )


def _memory_embedding_enqueue(config: CoreServiceConfig, memory_id: int) -> dict[str, object]:
    if memory_embeddings.embedding_config_from_settings(config) is None:
        raise InvalidInputError("memory embeddings require MINX_OPENROUTER_API_KEY")
    mid = _coerce_memory_id(memory_id)
    with scoped_connection(Path(config.db_path)) as conn:
        job = enqueue_memory_embedding(conn, mid)
        return {"job": _enrichment_job_as_dict(job)}


def _memory_embedding_status(config: CoreServiceConfig) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return {"status": memory_embedding_status(conn)}


def _memory_edge_create(
    config: CoreServiceConfig,
    source_memory_id: int,
    target_memory_id: int,
    predicate: str,
    relation_note: str,
) -> dict[str, object]:
    source_id = _coerce_memory_id(source_memory_id, field_name="source_memory_id")
    target_id = _coerce_memory_id(target_memory_id, field_name="target_memory_id")
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        edge = service.create_memory_edge(
            source_memory_id=source_id,
            target_memory_id=target_id,
            predicate=predicate,
            relation_note=relation_note,
            actor="user",
        )
        return {"edge": memory_edge_as_dict(edge)}


def _memory_edge_list(
    config: CoreServiceConfig,
    memory_id: int,
    direction: str,
    predicate: str | None,
    limit: int,
) -> dict[str, object]:
    mid = _coerce_memory_id(memory_id)
    effective_predicate = _normalize_optional_filter(predicate)
    lim = coerce_limit(limit, maximum=100)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        edges = service.list_memory_edges(
            mid,
            direction=direction,
            predicate=effective_predicate,
            limit=lim,
        )
        return {"edges": [memory_edge_as_dict(edge) for edge in edges]}


def _memory_edge_delete(config: CoreServiceConfig, edge_id: int) -> dict[str, object]:
    eid = _coerce_memory_id(edge_id, field_name="edge_id")
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        return {"deleted": service.delete_memory_edge(eid)}


def _enrichment_job_as_dict(job: EnrichmentJob) -> dict[str, object]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "subject_type": job.subject_type,
        "subject_id": job.subject_id,
        "payload_json": job.payload_json,
        "status": job.status,
        "priority": job.priority,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "available_at": job.available_at,
        "locked_at": job.locked_at,
        "completed_at": job.completed_at,
        "last_error": job.last_error,
        "result_json": job.result_json,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _get_pending_memory_candidates(
    config: CoreServiceConfig,
    scope: str | None,
    limit: int,
) -> dict[str, object]:
    effective_scope = _normalize_optional_filter(scope)
    lim = coerce_limit(limit, maximum=500)
    with scoped_connection(Path(config.db_path)) as conn:
        service = MemoryService(Path(config.db_path), conn=conn)
        service.prune_expired_memories()
        conn.commit()
        rows = service.list_pending_candidates(scope=effective_scope, limit=lim)
        return {"memories": [memory_record_as_dict(r) for r in rows]}


def _normalize_optional_filter(value: object, *, field_name: str = "filter") -> str | None:
    """Treat whitespace-only strings as "no filter" for MCP tool ergonomics."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInputError(f"{field_name} must be a string")
    stripped = value.strip()
    return stripped or None


def _coerce_memory_id(memory_id: int, *, field_name: str = "memory_id") -> int:
    if not isinstance(memory_id, int) or isinstance(memory_id, bool):
        raise InvalidInputError(f"{field_name} must be an integer")
    if memory_id < 1:
        raise InvalidInputError(f"{field_name} must be positive")
    return memory_id


def _coerce_confidence(value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidInputError("confidence must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > 1:
        raise InvalidInputError("confidence must be between 0 and 1 inclusive")
    return result
