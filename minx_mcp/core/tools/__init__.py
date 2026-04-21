"""Tool registration modules for the core MCP server.

Each submodule defines a ``register_<domain>_tools(mcp, config)`` function
that registers that domain's ``@mcp.tool`` / ``@mcp.resource`` endpoints
on a shared :class:`FastMCP` instance. ``minx_mcp.core.server`` composes
them into the final server.

This is a pure reorganization of what used to be one ~930-line file; tool
names, signatures, and behavior are unchanged.
"""
