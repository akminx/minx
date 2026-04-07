from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Protocol

from mcp.server.fastmcp import FastMCP

from minx_mcp.contracts import InvalidInputError, wrap_tool_call
from minx_mcp.core.models import ReviewContext
from minx_mcp.core.review import generate_daily_review, render_daily_review_markdown
from minx_mcp.vault_writer import VaultWriter


class CoreServiceConfig(Protocol):
    @property
    def db_path(self) -> Path: ...

    @property
    def vault_path(self) -> Path: ...


def create_core_server(config: CoreServiceConfig) -> FastMCP:
    mcp = FastMCP("minx-core", stateless_http=True, json_response=True)

    @mcp.tool(name="daily_review")
    def daily_review(
        review_date: str | None = None,
        force: bool = False,
    ) -> dict[str, object]:
        return wrap_tool_call(
            lambda: _daily_review(config, review_date, force),
        )

    return mcp


def _daily_review(
    config: CoreServiceConfig,
    review_date: str | None,
    force: bool,
) -> dict[str, object]:
    effective_date = review_date or date.today().isoformat()
    try:
        date.fromisoformat(effective_date)
    except ValueError as exc:
        raise InvalidInputError("review_date must be a valid ISO date") from exc

    ctx = ReviewContext(
        db_path=config.db_path,
        finance_api=None,
        vault_writer=VaultWriter(config.vault_path, ("Minx",)),
        llm=None,
    )

    loop = asyncio.new_event_loop()
    try:
        artifact = loop.run_until_complete(
            generate_daily_review(effective_date, ctx, force=force),
        )
    finally:
        loop.close()

    return {
        "date": artifact.date,
        "narrative": artifact.narrative,
        "next_day_focus": artifact.next_day_focus,
        "insight_count": len(artifact.insights),
        "llm_enriched": artifact.llm_enriched,
        "timeline_entry_count": len(artifact.timeline.entries),
        "open_loop_count": len(artifact.open_loops.loops),
        "markdown": render_daily_review_markdown(artifact),
    }
