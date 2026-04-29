"""FTS-backed memory search helpers."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Connection
from typing import Any

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.memory_models import MemoryRecord
from minx_mcp.validation import InvalidPayloadJSONError, require_non_empty

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemorySearchResult:
    memory: MemoryRecord
    rank: float
    snippet: str


def search_memories(
    conn: Connection,
    *,
    query: str,
    scope: str | None,
    memory_type: str | None,
    status: str | None,
    limit: int,
    allowed_status: frozenset[str],
    utc_reference_iso: Callable[[datetime | None], str],
    row_to_record: Callable[[Any], MemoryRecord],
    validate_search_limit: Callable[[int], None],
) -> list[MemorySearchResult]:
    cleaned_query = require_non_empty("query", query)
    validate_search_limit(limit)
    validate_fts_query_syntax(conn, cleaned_query)
    clauses = ["memory_fts MATCH ?"]
    params: list[object] = [cleaned_query]
    if status is not None:
        if status not in allowed_status:
            raise InvalidInputError(f"status must be one of {sorted(allowed_status)}")
        clauses.append("memories.status = ?")
        params.append(status)
    if status != "expired":
        clauses.append("(memories.expires_at IS NULL OR memories.expires_at > ?)")
        params.append(utc_reference_iso(None))
    if memory_type is not None:
        clauses.append("memories.memory_type = ?")
        params.append(require_non_empty("memory_type", memory_type))
    if scope is not None:
        clauses.append("memories.scope = ?")
        params.append(require_non_empty("scope", scope))
    sql = f"""
        SELECT
            memories.*,
            bm25(memory_fts) AS search_rank,
            snippet(memory_fts, -1, '<mark>', '</mark>', '...', 12) AS search_snippet
        FROM memory_fts
        JOIN memories ON memories.id = memory_fts.rowid
        WHERE {' AND '.join(clauses)}
        ORDER BY search_rank ASC, memories.updated_at DESC, memories.id DESC
        LIMIT ?
    """  # noqa: S608 - clauses are static fragments; all values are bound params.
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if is_fts_query_syntax_error(exc):
            raise InvalidInputError("query is not valid FTS5 syntax") from exc
        raise
    results: list[MemorySearchResult] = []
    for row in rows:
        try:
            memory = row_to_record(row)
        except InvalidPayloadJSONError as exc:
            logger.warning(
                "skipping memory FTS hit with corrupt payload_json",
                extra={"memory_id": exc.source_id, "label": exc.label},
            )
            continue
        results.append(
            MemorySearchResult(
                memory=memory,
                rank=float(row["search_rank"]),
                snippet=str(row["search_snippet"] or ""),
            )
        )
    return results


def is_fts_query_syntax_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return (
        "fts5: syntax error" in message
        or "unterminated string" in message
        or "malformed match expression" in message
    )


def validate_fts_query_syntax(conn: Connection, query: str) -> None:
    try:
        conn.execute(
            "SELECT rowid FROM memory_fts WHERE memory_fts MATCH ? LIMIT 1",
            (query,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if is_fts_query_syntax_error(exc):
            raise InvalidInputError("query is not valid FTS5 syntax") from exc
        raise
