from __future__ import annotations

import argparse

from minx_mcp.config import get_settings
from minx_mcp.core.server import create_core_server
from minx_mcp.transport import run_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "http"], default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    return parser


def main() -> None:
    settings = get_settings()
    args = build_parser().parse_args()
    server = create_core_server(settings)
    run_server(
        server,
        transport=args.transport or settings.default_transport,
        host=args.host or settings.http_host,
        port=args.port or settings.http_port,
    )


if __name__ == "__main__":
    main()
