from __future__ import annotations

from typing import cast

from minx_mcp.config import get_settings
from minx_mcp.entrypoint import build_parser
from minx_mcp.finance.server import create_finance_server
from minx_mcp.finance.service import FinanceService
from minx_mcp.logging_config import configure_logging
from minx_mcp.transport import MCPServerLike, TransportName, run_server


def main() -> None:
    configure_logging()
    settings = get_settings()
    args = build_parser().parse_args()
    service = FinanceService(settings.db_path, settings.vault_path, settings.staging_path)
    server = cast(MCPServerLike, create_finance_server(service))
    transport = cast(TransportName, args.transport or settings.default_transport)
    run_server(
        server,
        transport=transport,
        host=args.host or settings.http_host,
        port=args.port or settings.http_port,
    )


if __name__ == "__main__":
    main()
