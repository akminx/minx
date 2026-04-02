from mcp.server.fastmcp import FastMCP

from minx_mcp.db import get_connection
from minx_mcp.finance.server import SAFE_TOOLS, SENSITIVE_TOOLS, create_finance_server
from minx_mcp.finance.service import FinanceService


def test_finance_server_registers_expected_tool_names(tmp_path):
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path / "vault")
    server = create_finance_server(service)
    assert isinstance(server, FastMCP)
    assert SAFE_TOOLS == [
        "safe_finance_summary",
        "safe_finance_accounts",
        "finance_import",
        "finance_categorize",
        "finance_anomalies",
        "finance_job_status",
        "finance_generate_weekly_report",
        "finance_generate_monthly_report",
    ]
    assert SENSITIVE_TOOLS == ["sensitive_finance_query"]


def test_streamable_http_app_is_available(tmp_path):
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path / "vault")
    server = create_finance_server(service)
    app = server.streamable_http_app()
    assert callable(app)
