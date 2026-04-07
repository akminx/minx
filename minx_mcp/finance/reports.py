"""Compatibility facade: re-exports report builders, persistence, and rendering."""

from __future__ import annotations

from minx_mcp.finance.report_builders import build_monthly_report, build_weekly_report
from minx_mcp.finance.report_persistence import (
    REPORT_RUN_STATUSES,
    persist_report_run,
    upsert_report_run,
)
from minx_mcp.finance.report_rendering import (
    _render,
    render_monthly_markdown,
    render_weekly_markdown,
)

__all__ = [
    "REPORT_RUN_STATUSES",
    "build_monthly_report",
    "build_weekly_report",
    "persist_report_run",
    "render_monthly_markdown",
    "render_weekly_markdown",
    "upsert_report_run",
    "_render",
]
