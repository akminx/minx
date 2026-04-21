"""Core MCP server composition root.

The per-domain tool registrations live in :mod:`minx_mcp.core.tools`;
this module just constructs the :class:`FastMCP` instance and wires the
``register_*_tools`` functions onto it. Prior to this split the file
held all ~900 lines of tool bodies inline.

Back-compat: ``CoreServiceConfig`` is re-exported here so existing
imports (``from minx_mcp.core.server import CoreServiceConfig``) keep
working.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from minx_mcp.core.tools._shared import CoreServiceConfig
from minx_mcp.core.tools.goals import register_goal_tools
from minx_mcp.core.tools.memory import register_memory_tools
from minx_mcp.core.tools.snapshot import register_snapshot_tools
from minx_mcp.core.tools.vault import register_vault_tools

__all__ = ["CoreServiceConfig", "create_core_server"]


def create_core_server(config: CoreServiceConfig) -> FastMCP:
    mcp = FastMCP("minx-core", stateless_http=True, json_response=True)
    register_snapshot_tools(mcp, config)
    register_goal_tools(mcp, config)
    register_vault_tools(mcp, config)
    register_memory_tools(mcp, config)
    return mcp
