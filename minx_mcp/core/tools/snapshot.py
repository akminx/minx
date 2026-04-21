"""Snapshot / insight-history / snapshot-archive MCP tools."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import (
    InvalidInputError,
    NotFoundError,
    ToolResponse,
    wrap_async_tool_call,
    wrap_tool_call,
)
from minx_mcp.core.history import get_insight_history
from minx_mcp.core.models import SnapshotContext
from minx_mcp.core.snapshot import build_daily_snapshot
from minx_mcp.core.tools._shared import CoreServiceConfig, coerce_limit, resolve_review_date
from minx_mcp.db import scoped_connection
from minx_mcp.validation import validate_iso_date

__all__ = ["register_snapshot_tools"]


def register_snapshot_tools(mcp: FastMCP, config: CoreServiceConfig) -> None:
    @mcp.tool(name="get_daily_snapshot")
    async def get_daily_snapshot_tool(
        review_date: str | None = None,
        force: bool = False,
    ) -> ToolResponse:
        return await wrap_async_tool_call(
            lambda: _daily_snapshot(config, review_date, force),
            tool_name="get_daily_snapshot",
        )

    @mcp.tool(name="get_insight_history")
    def insight_history(
        days: int = 28,
        insight_type: str | None = None,
        goal_id: int | None = None,
        end_date: str | None = None,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: get_insight_history(
                config.db_path,
                days=days,
                insight_type=insight_type,
                goal_id=goal_id,
                end_date=end_date,
            ),
            tool_name="get_insight_history",
        )

    @mcp.tool(name="list_snapshot_archives")
    def list_snapshot_archives_tool(
        review_date: str | None = None,
        limit: int = 30,
    ) -> ToolResponse:
        return wrap_tool_call(
            lambda: _list_snapshot_archives(config, review_date, limit),
            tool_name="list_snapshot_archives",
        )

    @mcp.tool(name="get_snapshot_archive")
    def get_snapshot_archive_tool(archive_id: int) -> ToolResponse:
        return wrap_tool_call(
            lambda: _get_snapshot_archive(config, archive_id),
            tool_name="get_snapshot_archive",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _daily_snapshot(
    config: CoreServiceConfig,
    review_date: str | None,
    force: bool,
) -> dict[str, object]:
    effective_date = resolve_review_date(review_date)
    ctx = SnapshotContext(
        db_path=config.db_path,
        finance_api=None,
    )
    artifact = await build_daily_snapshot(effective_date, ctx, force=force)
    data = asdict(artifact)
    if data["persistence_warning"] is None:
        data.pop("persistence_warning")
    return data


def _list_snapshot_archives(
    config: CoreServiceConfig,
    review_date: str | None,
    limit: int,
) -> dict[str, object]:
    lim = coerce_limit(limit, maximum=500)
    filter_date: str | None = None
    if review_date is not None:
        stripped = review_date.strip()
        if stripped == "":
            raise InvalidInputError("review_date must not be empty when provided")
        filter_date = validate_iso_date(stripped, field_name="review_date").isoformat()
    with scoped_connection(Path(config.db_path)) as conn:
        if filter_date is None:
            rows = conn.execute(
                """
                SELECT id, review_date, generated_at, content_hash, source
                FROM snapshot_archives
                ORDER BY generated_at DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, review_date, generated_at, content_hash, source
                FROM snapshot_archives
                WHERE review_date = ?
                ORDER BY generated_at DESC
                LIMIT ?
                """,
                (filter_date, lim),
            ).fetchall()
        archives = [
            {
                "id": int(row["id"]),
                "review_date": row["review_date"],
                "generated_at": row["generated_at"],
                "content_hash": row["content_hash"],
                "source": row["source"],
            }
            for row in rows
        ]
        return {"archives": archives}


def _get_snapshot_archive(config: CoreServiceConfig, archive_id: int) -> dict[str, object]:
    aid = _coerce_archive_id(archive_id)
    with scoped_connection(Path(config.db_path)) as conn:
        row = conn.execute(
            """
            SELECT id, review_date, generated_at, snapshot_json, content_hash, source
            FROM snapshot_archives
            WHERE id = ?
            """,
            (aid,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"snapshot archive {aid} not found")
        snapshot_payload = json.loads(row["snapshot_json"])
        return {
            "archive": {
                "id": int(row["id"]),
                "review_date": row["review_date"],
                "generated_at": row["generated_at"],
                "content_hash": row["content_hash"],
                "source": row["source"],
                "snapshot": snapshot_payload,
            }
        }


def _coerce_archive_id(archive_id: int) -> int:
    if not isinstance(archive_id, int) or isinstance(archive_id, bool):
        raise InvalidInputError("archive_id must be an integer")
    if archive_id < 1:
        raise InvalidInputError("archive_id must be positive")
    return archive_id
