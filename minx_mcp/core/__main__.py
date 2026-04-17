from __future__ import annotations

from minx_mcp.core.server import create_core_server
from minx_mcp.entrypoint import run_domain_server

if __name__ == "__main__":
    run_domain_server(create_core_server)
