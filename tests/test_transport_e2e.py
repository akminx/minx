from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from minx_mcp.db import get_connection


@pytest.mark.slow
@pytest.mark.asyncio
async def test_finance_server_stdio_transport_smoke(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    conn = get_connection(db_path)
    conn.close()

    env = os.environ.copy()
    env["MINX_DB_PATH"] = str(db_path)
    env["MINX_VAULT_PATH"] = str(tmp_path / "vault")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "minx_mcp.finance", "--transport", "stdio"],
        env=env,
        cwd=Path.cwd(),
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        initialize_result = await session.initialize()
        assert initialize_result.serverInfo.name == "minx-finance"

        tools_result = await session.list_tools()
        assert any(tool.name == "safe_finance_summary" for tool in tools_result.tools)

        summary_result = await session.call_tool("safe_finance_summary", {})
        assert summary_result.isError is False
        assert summary_result.structuredContent["success"] is True
        assert "net_total" in summary_result.structuredContent["data"]
