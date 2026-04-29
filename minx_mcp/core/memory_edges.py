"""Memory graph edge helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from sqlite3 import Connection
from typing import Any

from minx_mcp.contracts import ConflictError, InvalidInputError
from minx_mcp.core.secret_scanner import SecretVerdictKind, redact_secrets
from minx_mcp.validation import require_non_empty

_ALLOWED_EDGE_PREDICATES = frozenset({"supersedes", "contradicts", "cites"})
_ALLOWED_EDGE_DIRECTIONS = frozenset({"incoming", "outgoing", "both"})


@dataclass(frozen=True)
class MemoryEdge:
    id: int
    source_memory_id: int
    target_memory_id: int
    predicate: str
    relation_note: str
    actor: str
    created_at: str
    updated_at: str


def create_memory_edge(
    conn: Connection,
    *,
    source_memory_id: int,
    target_memory_id: int,
    predicate: str,
    relation_note: str,
    actor: str,
    validate_actor: Callable[[str], None],
    validate_positive_int: Callable[[str, int], int],
    require_memory_exists: Callable[[int], None],
    get_memory_edge: Callable[[int], MemoryEdge | None],
) -> MemoryEdge:
    validate_actor(actor)
    source_id = validate_positive_int("source_memory_id", source_memory_id)
    target_id = validate_positive_int("target_memory_id", target_memory_id)
    if source_id == target_id:
        raise InvalidInputError("source_memory_id and target_memory_id must differ")
    pred = validate_edge_predicate(predicate)
    note = scan_edge_relation_note(relation_note)
    require_memory_exists(source_id)
    require_memory_exists(target_id)
    try:
        cur = conn.execute(
            """
            INSERT INTO memory_edges (
                source_memory_id, target_memory_id, predicate, relation_note, actor
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (source_id, target_id, pred, note, actor),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ConflictError(
            "memory edge already exists",
            data={
                "conflict_kind": "memory_edge",
                "source_memory_id": source_id,
                "target_memory_id": target_id,
                "predicate": pred,
            },
        ) from exc
    if cur.lastrowid is None:
        raise RuntimeError("memory edge insert did not return a row id")
    edge_id = int(cur.lastrowid)
    edge = get_memory_edge(edge_id)
    if edge is None:
        raise RuntimeError("memory edge insert did not return a readable row")
    return edge


def get_memory_edge(
    conn: Connection,
    *,
    edge_id: int,
    validate_positive_int: Callable[[str, int], int],
) -> MemoryEdge | None:
    eid = validate_positive_int("edge_id", edge_id)
    row = conn.execute("SELECT * FROM memory_edges WHERE id = ?", (eid,)).fetchone()
    if row is None:
        return None
    return row_to_edge(row)


def list_memory_edges(
    conn: Connection,
    memory_id: int,
    *,
    direction: str,
    predicate: str | None,
    limit: int,
    validate_positive_int: Callable[[str, int], int],
    validate_search_limit: Callable[[int], None],
) -> list[MemoryEdge]:
    mid = validate_positive_int("memory_id", memory_id)
    validate_edge_direction(direction)
    validate_search_limit(limit)
    clauses: list[str] = []
    params: list[object] = []
    if direction == "incoming":
        clauses.append("target_memory_id = ?")
        params.append(mid)
    elif direction == "outgoing":
        clauses.append("source_memory_id = ?")
        params.append(mid)
    else:
        clauses.append("(source_memory_id = ? OR target_memory_id = ?)")
        params.extend((mid, mid))
    if predicate is not None:
        clauses.append("predicate = ?")
        params.append(validate_edge_predicate(predicate))
    sql = f"""
        SELECT *
        FROM memory_edges
        WHERE {' AND '.join(clauses)}
        ORDER BY id DESC
        LIMIT ?
    """  # noqa: S608 - clauses are fixed fragments; values are bound params.
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [row_to_edge(row) for row in rows]


def delete_memory_edge(
    conn: Connection,
    edge_id: int,
    *,
    validate_positive_int: Callable[[str, int], int],
) -> bool:
    eid = validate_positive_int("edge_id", edge_id)
    cur = conn.execute("DELETE FROM memory_edges WHERE id = ?", (eid,))
    conn.commit()
    return cur.rowcount > 0


def row_to_edge(row: Any) -> MemoryEdge:
    return MemoryEdge(
        id=int(row["id"]),
        source_memory_id=int(row["source_memory_id"]),
        target_memory_id=int(row["target_memory_id"]),
        predicate=str(row["predicate"]),
        relation_note=str(row["relation_note"]),
        actor=str(row["actor"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def validate_edge_predicate(predicate: str) -> str:
    pred = require_non_empty("predicate", predicate)
    if pred not in _ALLOWED_EDGE_PREDICATES:
        raise InvalidInputError(f"predicate must be one of {sorted(_ALLOWED_EDGE_PREDICATES)}")
    return pred


def validate_edge_direction(direction: str) -> None:
    if direction not in _ALLOWED_EDGE_DIRECTIONS:
        raise InvalidInputError(f"direction must be one of {sorted(_ALLOWED_EDGE_DIRECTIONS)}")


def scan_edge_relation_note(note: str) -> str:
    verdict = redact_secrets(note)
    if verdict.verdict is SecretVerdictKind.BLOCK:
        raise InvalidInputError(
            "Secret detected in memory edge input",
            data={
                "kind": "secret_detected",
                "verdict": "block",
                "surface": "memory_graph",
                "detected_kinds": sorted({finding.kind for finding in verdict.findings}),
                "locations": [
                    {"field": "relation_note", "start": finding.start, "end": finding.end}
                    for finding in verdict.findings
                ],
            },
        )
    return verdict.text
