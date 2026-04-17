from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any, cast

from mcp.server.fastmcp import FastMCP

from minx_mcp.config import Settings, get_settings
from minx_mcp.logging_config import configure_logging
from minx_mcp.transport import MCPServerLike, TransportName, run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "http"], default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


def run_domain_server(create_server_fn: Callable[[Settings], FastMCP | Any]) -> None:
    configure_logging()
    settings = get_settings()
    args = build_parser().parse_args()
    server = cast(MCPServerLike, create_server_fn(settings))
    transport = cast(TransportName, args.transport or settings.default_transport)
    run_server(
        server,
        transport=transport,
        host=args.host or settings.http_host,
        port=args.port or settings.http_port,
    )
