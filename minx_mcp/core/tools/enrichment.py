"""Enrichment queue MCP tools."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import InvalidInputError, ToolResponse, wrap_tool_call
from minx_mcp.core import memory_embeddings
from minx_mcp.core.enrichment_queue import (
    EnrichmentJob,
    enrichment_status,
    retry_dead_letter,
    sweep_enrichment_queue,
)
from minx_mcp.core.tools._shared import CoreServiceConfig, coerce_limit
from minx_mcp.db import scoped_connection

__all__ = ["register_enrichment_tools"]


def register_enrichment_tools(mcp: FastMCP, config: CoreServiceConfig) -> None:
    @mcp.tool(name="enrichment_sweep")
    def enrichment_sweep_tool(limit: int = 25) -> ToolResponse:
        return wrap_tool_call(
            lambda: _enrichment_sweep(config, limit),
            tool_name="enrichment_sweep",
        )

    @mcp.tool(name="enrichment_status")
    def enrichment_status_tool() -> ToolResponse:
        return wrap_tool_call(
            lambda: _enrichment_status(config),
            tool_name="enrichment_status",
        )

    @mcp.tool(name="enrichment_retry_dead_letter")
    def enrichment_retry_dead_letter_tool(job_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _enrichment_retry_dead_letter(config, job_id),
            tool_name="enrichment_retry_dead_letter",
        )


def _enrichment_sweep(config: CoreServiceConfig, limit: int) -> dict[str, object]:
    lim = coerce_limit(limit, maximum=100)
    with scoped_connection(Path(config.db_path)) as conn:
        embedding_config = memory_embeddings.embedding_config_from_settings(config)
        handlers = (
            memory_embeddings.memory_embedding_sweep_handlers(
                conn,
                config=embedding_config,
                embed=memory_embeddings.openrouter_embedder(embedding_config),
            )
            if embedding_config is not None
            else {}
        )
        report = sweep_enrichment_queue(conn, limit=lim, handlers=handlers)
        return {
            "report": {
                "claimed": report.claimed,
                "succeeded": report.succeeded,
                "failed": report.failed,
                "dead_lettered": report.dead_lettered,
            }
        }


def _enrichment_status(config: CoreServiceConfig) -> dict[str, object]:
    with scoped_connection(Path(config.db_path)) as conn:
        return {"counts": enrichment_status(conn)}


def _enrichment_retry_dead_letter(config: CoreServiceConfig, job_id: int) -> dict[str, object]:
    if not isinstance(job_id, int) or isinstance(job_id, bool):
        raise InvalidInputError("job_id must be an integer")
    with scoped_connection(Path(config.db_path)) as conn:
        job = retry_dead_letter(conn, job_id)
        return {"job": _job_as_dict(job)}


def _job_as_dict(job: EnrichmentJob) -> dict[str, object]:
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
