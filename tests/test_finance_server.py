import argparse

from mcp.server.fastmcp import FastMCP

from minx_mcp.finance import __main__ as finance_main
from minx_mcp.finance.server import SAFE_TOOLS, SENSITIVE_TOOLS, create_finance_server
from minx_mcp.finance.service import FinanceService


def test_finance_server_registers_expected_tool_names(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    assert isinstance(server, FastMCP)
    assert SAFE_TOOLS == [
        "safe_finance_summary",
        "safe_finance_accounts",
        "finance_import",
        "finance_categorize",
        "finance_add_category_rule",
        "finance_anomalies",
        "finance_job_status",
        "finance_generate_weekly_report",
        "finance_generate_monthly_report",
    ]
    assert SENSITIVE_TOOLS == ["sensitive_finance_query"]


def test_streamable_http_app_is_available(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    app = server.streamable_http_app()
    assert callable(app)


def test_build_parser_accepts_transport_host_and_port():
    parser = finance_main.build_parser()

    args = parser.parse_args(["--transport", "http", "--host", "0.0.0.0", "--port", "9000"])

    assert args.transport == "http"
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_main_wires_cli_and_settings_into_run_server(monkeypatch, tmp_path):
    calls = {}
    fake_server = object()

    class Settings:
        db_path = tmp_path / "minx.db"
        vault_path = tmp_path / "vault"
        default_transport = "stdio"
        http_host = "127.0.0.1"
        http_port = 8000

    def fake_get_settings():
        return Settings()

    def fake_create_finance_server(service):
        calls["service"] = service
        return fake_server

    def fake_run_server(server, transport, host, port):
        calls["server"] = server
        calls["transport"] = transport
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr(finance_main, "get_settings", fake_get_settings)
    monkeypatch.setattr(finance_main, "create_finance_server", fake_create_finance_server)
    monkeypatch.setattr(finance_main, "run_server", fake_run_server)
    monkeypatch.setattr(
        finance_main,
        "build_parser",
        lambda: argparse.ArgumentParser(prog="minx-finance"),
    )
    monkeypatch.setattr(
        argparse.ArgumentParser,
        "parse_args",
        lambda self: argparse.Namespace(transport="http", host="0.0.0.0", port=9000),
    )

    finance_main.main()

    assert isinstance(calls["service"], FinanceService)
    assert calls["server"] is fake_server
    assert calls["transport"] == "http"
    assert calls["host"] == "0.0.0.0"
    assert calls["port"] == 9000
