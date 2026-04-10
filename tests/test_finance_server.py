import argparse
import asyncio
import inspect

import pytest

from mcp.server.fastmcp import FastMCP

from minx_mcp.finance import __main__ as finance_main
from minx_mcp.finance.server import SAFE_TOOLS, SENSITIVE_TOOLS, create_finance_server
from minx_mcp.finance.service import FinanceService


class _StubFinanceQueryLLM:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def run_json_prompt(self, prompt: str) -> str:
        assert "Whole Foods" in prompt
        return self.payload


def _call_tool_sync(fn, *args, **kwargs):
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def test_finance_server_registers_expected_tool_names(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    assert isinstance(server, FastMCP)
    assert SAFE_TOOLS == [
        "safe_finance_summary",
        "safe_finance_accounts",
        "finance_import",
        "finance_import_preview",
        "finance_categorize",
        "finance_add_category_rule",
        "finance_anomalies",
        "finance_monitoring",
        "finance_job_status",
        "finance_generate_weekly_report",
        "finance_generate_monthly_report",
    ]
    assert SENSITIVE_TOOLS == ["sensitive_finance_query", "finance_query"]


def test_finance_server_registers_phase2_safe_tool_names(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)

    assert server._tool_manager.get_tool("finance_import_preview").name == "finance_import_preview"
    assert server._tool_manager.get_tool("finance_monitoring").name == "finance_monitoring"


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
        staging_path = tmp_path / "staging"
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


def test_finance_import_tool_rejects_missing_source_file(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", import_root=tmp_path)
    server = create_finance_server(service)
    finance_import = server._tool_manager.get_tool("finance_import").fn

    result = finance_import(str(tmp_path / "missing.csv"), "DCU")

    assert result == {
        "success": False,
        "data": None,
        "error": "source_ref must point to an existing file",
        "error_code": "INVALID_INPUT",
    }



def test_finance_import_tool_rejects_unknown_source_kind(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", import_root=tmp_path)
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    server = create_finance_server(service)
    finance_import = server._tool_manager.get_tool("finance_import").fn

    result = finance_import(str(source), "DCU", source_kind="weird_kind")

    assert result == {
        "success": False,
        "data": None,
        "error": "Unsupported finance source kind: weird_kind",
        "error_code": "INVALID_INPUT",
    }


def test_finance_import_tool_rejects_unsupported_file_before_reading_contents(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", import_root=tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("not a finance file")
    server = create_finance_server(service)
    finance_import = server._tool_manager.get_tool("finance_import").fn

    result = finance_import(str(source), "DCU")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"
    assert "Could not detect finance source" in result["error"]


def test_finance_import_tool_loads_saved_mapping_for_generic_csv(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", import_root=tmp_path)
    source = tmp_path / "transactions.csv"
    source.write_text(
        "posted,description,amount\n"
        "03/02/2026,Coffee,-12.50\n"
    )
    service.conn.execute(
        """
        INSERT INTO preferences (domain, key, value_json, updated_at)
        VALUES (
            'finance.csv_mapping',
            'DCU',
            '{\"date_column\": \"posted\", \"date_format\": \"%m/%d/%Y\", \"description_column\": \"description\", \"amount_column\": \"amount\"}',
            datetime('now')
        )
        """
    )
    service.conn.commit()
    server = create_finance_server(service)
    finance_import = server._tool_manager.get_tool("finance_import").fn

    result = finance_import(str(source), "DCU", source_kind="generic_csv")

    assert result["success"] is True
    assert result["data"]["result"]["inserted"] == 1


def test_finance_import_tool_loads_saved_mapping_by_account_import_profile(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", import_root=tmp_path)
    source = tmp_path / "transactions.csv"
    source.write_text(
        "posted,description,amount\n"
        "03/02/2026,Coffee,-12.50\n"
    )
    service.conn.execute(
        """
        INSERT INTO preferences (domain, key, value_json, updated_at)
        VALUES (
            'finance.csv_mapping',
            'dcu',
            '{\"date_column\": \"posted\", \"date_format\": \"%m/%d/%Y\", \"description_column\": \"description\", \"amount_column\": \"amount\"}',
            datetime('now')
        )
        """
    )
    service.conn.commit()
    server = create_finance_server(service)
    finance_import = server._tool_manager.get_tool("finance_import").fn

    result = finance_import(str(source), "DCU", source_kind="generic_csv")

    assert result["success"] is True
    assert result["data"]["result"]["inserted"] == 1


def test_finance_categorize_tool_rejects_empty_transaction_ids(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    finance_categorize = server._tool_manager.get_tool("finance_categorize").fn

    result = finance_categorize([], "Groceries")

    assert result == {
        "success": False,
        "data": None,
        "error": "transaction_ids must be a non-empty list",
        "error_code": "INVALID_INPUT",
    }



def test_finance_categorize_tool_rejects_unknown_transaction_ids(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    finance_categorize = server._tool_manager.get_tool("finance_categorize").fn

    result = finance_categorize([999], "Groceries")

    assert result == {
        "success": False,
        "data": None,
        "error": "Unknown finance transaction ids: 999",
        "error_code": "NOT_FOUND",
    }


def test_finance_categorize_tool_rejects_unknown_category(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    service.finance_import(str(source), account_name="DCU")
    tx_id = service.sensitive_finance_query(limit=1)["transactions"][0]["id"]
    server = create_finance_server(service)
    finance_categorize = server._tool_manager.get_tool("finance_categorize").fn

    result = finance_categorize([tx_id], "Missing Category")

    assert result == {
        "success": False,
        "data": None,
        "error": "Unknown finance category: Missing Category",
        "error_code": "NOT_FOUND",
    }


def test_finance_categorize_tool_reports_actual_rows_updated(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    service.finance_import(str(source), account_name="DCU")
    tx_id = service.sensitive_finance_query(limit=1)["transactions"][0]["id"]
    server = create_finance_server(service)
    finance_categorize = server._tool_manager.get_tool("finance_categorize").fn

    result = finance_categorize([tx_id, tx_id], "Groceries")

    assert result == {
        "success": True,
        "data": {"updated": 1},
        "error": None,
        "error_code": None,
    }


def test_sensitive_finance_query_tool_accepts_filters(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    groceries_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Groceries'"
    ).fetchone()["id"]
    account_id = service.conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'DCU'"
    ).fetchone()["id"]
    service.conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, ?, 'csv', 'seed.csv', 'fp')
        """,
        (account_id,),
    )
    service.conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, 1, '2026-03-02', 'H-E-B Grocery', 'H-E-B', -4520, ?, 'manual')
        """,
        (account_id, groceries_id),
    )
    service.conn.commit()
    server = create_finance_server(service)
    sensitive_finance_query = server._tool_manager.get_tool("sensitive_finance_query").fn

    result = sensitive_finance_query(
        limit=5,
        start_date="2026-03-01",
        end_date="2026-03-31",
        category_name="Groceries",
        merchant="H-E-B",
        account_name="DCU",
        description_contains="grocery",
    )

    assert result == {
        "success": True,
        "data": {
            "transactions": [
                {
                    "id": 1,
                    "posted_at": "2026-03-02",
                    "description": "H-E-B Grocery",
                    "merchant": "H-E-B",
                    "raw_merchant": None,
                    "account_name": "DCU",
                    "category_name": "Groceries",
                    "amount": -45.2,
                }
            ]
        },
        "error": None,
        "error_code": None,
    }


def test_finance_query_tool_executes_validated_query_plan(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    groceries_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Groceries'"
    ).fetchone()["id"]
    account_id = service.conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'DCU'"
    ).fetchone()["id"]
    service.conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, ?, 'csv', 'seed.csv', 'fp')
        """,
        (account_id,),
    )
    service.conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, 1, '2026-03-12', 'Whole Foods Market', 'Whole Foods', -4520, ?, 'manual')
        """,
        (account_id, groceries_id),
    )
    service.conn.commit()
    server = create_finance_server(
        service,
        llm=_StubFinanceQueryLLM(
            (
                '{"intent":"list_transactions","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31","merchant":"Whole Foods"},'
                '"confidence":0.94,"needs_clarification":false}'
            )
        ),
    )
    finance_query = server._tool_manager.get_tool("finance_query").fn

    result = _call_tool_sync(
        finance_query,
        "show me everything at Whole Foods last month",
        "2026-03-31",
    )

    assert result == {
        "success": True,
        "data": {
            "result_type": "query",
            "intent": "list_transactions",
            "filters": {
                "start_date": "2026-03-01",
                "end_date": "2026-03-31",
                "merchant": "Whole Foods",
            },
            "confidence": 0.94,
            "transactions": [
                {
                    "id": 1,
                    "posted_at": "2026-03-12",
                    "description": "Whole Foods Market",
                    "merchant": "Whole Foods",
                    "raw_merchant": None,
                    "account_name": "DCU",
                    "category_name": "Groceries",
                    "amount": -45.2,
                }
            ],
        },
        "error": None,
        "error_code": None,
    }


def test_finance_query_structured_path_rejects_unknown_canonical_filter_values(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    finance_query = server._tool_manager.get_tool("finance_query").fn

    for kwargs in (
        {"intent": "list_transactions", "filters": {"merchant": "Whole Fuds"}},
        {"intent": "list_transactions", "filters": {"category_name": "Groceriez"}},
        {"intent": "list_transactions", "filters": {"account_name": "Checkinggg"}},
    ):
        result = _call_tool_sync(finance_query, **kwargs)
        assert result["success"] is False
        assert result["error_code"] == "INVALID_INPUT"


def test_finance_query_rejects_injected_non_json_capable_llm(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service, llm=object())
    finance_query = server._tool_manager.get_tool("finance_query").fn

    result = _call_tool_sync(finance_query, "show me transactions", "2026-03-31")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_finance_query_legacy_blank_message_reports_message_field_name(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service, llm=object())
    finance_query = server._tool_manager.get_tool("finance_query").fn

    result = _call_tool_sync(finance_query, "   ", "2026-03-31")

    assert result == {
        "success": False,
        "data": None,
        "error": "message must not be empty",
        "error_code": "INVALID_INPUT",
    }


def test_finance_query_uses_service_db_path_for_default_llm_resolution(tmp_path, monkeypatch):
    calls: dict[str, object] = {}

    class _ConfiguredLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            return (
                '{"intent":"list_transactions","filters":{},'
                '"confidence":0.9,"needs_clarification":true,'
                '"clarification_type":"missing_date_range",'
                '"question":"Which date range should I use?"}'
            )

    class _ProtocolOnlyService:
        def __init__(self, db_path):
            self.db_path = db_path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def safe_finance_summary(self):
            return {}

        def list_accounts(self):
            return {"accounts": []}

        def list_account_names(self):
            return ["DCU"]

        def list_transaction_category_names(self):
            return ["Groceries"]

        def list_spending_merchant_names(self):
            return ["Whole Foods"]

        def finance_import(self, source_ref, account_name, source_kind=None):
            raise NotImplementedError

        def missing_transaction_ids(self, transaction_ids):
            return []

        def finance_categorize(self, transaction_ids, category_name):
            raise NotImplementedError

        def add_category_rule(self, category_name, match_kind, pattern):
            raise NotImplementedError

        def finance_anomalies(self):
            return {"items": []}

        def get_job(self, job_id):
            raise NotImplementedError

        def generate_weekly_report(self, period_start, period_end):
            raise NotImplementedError

        def generate_monthly_report(self, period_start, period_end):
            raise NotImplementedError

        def sensitive_finance_query(self, limit=50, session_ref=None, **filters):
            return {"transactions": []}

        def get_filtered_spending_total(self, **filters):
            return 0

        def get_filtered_transaction_count(self, **filters):
            return 0

    def fake_create_llm(config=None, *, db_path=None):
        calls["db_path"] = db_path
        return _ConfiguredLLM()

    monkeypatch.setattr("minx_mcp.finance.server.create_llm", fake_create_llm)
    service = _ProtocolOnlyService(tmp_path / "custom.db")
    server = create_finance_server(service)
    finance_query = server._tool_manager.get_tool("finance_query").fn

    result = _call_tool_sync(finance_query, "show me Whole Foods transactions", "2026-03-31")

    assert calls["db_path"] == tmp_path / "custom.db"
    assert result == {
        "success": True,
        "data": {
            "result_type": "clarify",
            "intent": "list_transactions",
            "filters": {},
            "confidence": 0.9,
            "clarification_type": "missing_date_range",
            "question": "Which date range should I use?",
            "options": None,
        },
        "error": None,
        "error_code": None,
    }


@pytest.mark.asyncio
async def test_finance_query_tool_is_async_safe_inside_running_loop(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    groceries_id = service.conn.execute(
        "SELECT id FROM finance_categories WHERE name = 'Groceries'"
    ).fetchone()["id"]
    account_id = service.conn.execute(
        "SELECT id FROM finance_accounts WHERE name = 'DCU'"
    ).fetchone()["id"]
    service.conn.execute(
        """
        INSERT INTO finance_import_batches (id, account_id, source_type, source_ref, raw_fingerprint)
        VALUES (1, ?, 'csv', 'seed.csv', 'fp')
        """,
        (account_id,),
    )
    service.conn.execute(
        """
        INSERT INTO finance_transactions (
            account_id, batch_id, posted_at, description, merchant, amount_cents, category_id, category_source
        ) VALUES (?, 1, '2026-03-12', 'Whole Foods Market', 'Whole Foods', -4520, ?, 'manual')
        """,
        (account_id, groceries_id),
    )
    service.conn.commit()
    server = create_finance_server(
        service,
        llm=_StubFinanceQueryLLM(
            (
                '{"intent":"list_transactions","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31","merchant":"Whole Foods"},'
                '"confidence":0.94,"needs_clarification":false}'
            )
        ),
    )
    finance_query = server._tool_manager.get_tool("finance_query").fn

    result = await finance_query("show me everything at Whole Foods last month", "2026-03-31")

    assert result["success"] is True
    assert result["data"]["result_type"] == "query"
    assert result["data"]["filters"]["merchant"] == "Whole Foods"


def test_finance_job_status_returns_not_found_envelope_for_missing_job(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    finance_job_status = server._tool_manager.get_tool("finance_job_status").fn

    result = finance_job_status("missing-job")

    assert result == {
        "success": False,
        "data": None,
        "error": "Unknown finance job id: missing-job",
        "error_code": "NOT_FOUND",
    }


def test_finance_add_category_rule_tool_rejects_empty_pattern(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    finance_add_rule = server._tool_manager.get_tool("finance_add_category_rule").fn

    result = finance_add_rule("Groceries", "merchant_contains", "   ")

    assert result == {
        "success": False,
        "data": None,
        "error": "pattern must not be empty",
        "error_code": "INVALID_INPUT",
    }


def test_finance_add_category_rule_tool_rejects_unknown_category(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    finance_add_rule = server._tool_manager.get_tool("finance_add_category_rule").fn

    result = finance_add_rule("Missing Category", "merchant_contains", "H-E-B")

    assert result == {
        "success": False,
        "data": None,
        "error": "Unknown finance category: Missing Category",
        "error_code": "NOT_FOUND",
    }


def test_report_tools_reject_invalid_date_windows(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    weekly = server._tool_manager.get_tool("finance_generate_weekly_report").fn
    monthly = server._tool_manager.get_tool("finance_generate_monthly_report").fn

    assert weekly("2026-03-10", "2026-03-01") == {
        "success": False,
        "data": None,
        "error": "period_start must be on or before period_end",
        "error_code": "INVALID_INPUT",
    }

    assert monthly("03/01/2026", "2026-03-31") == {
        "success": False,
        "data": None,
        "error": "Invalid ISO date",
        "error_code": "INVALID_INPUT",
    }

    assert weekly("2026-03-01", "2026-03-05") == {
        "success": False,
        "data": None,
        "error": "weekly reports must span exactly 7 days",
        "error_code": "INVALID_INPUT",
    }

    assert monthly("2026-03-02", "2026-03-31") == {
        "success": False,
        "data": None,
        "error": "monthly reports must cover a full calendar month",
        "error_code": "INVALID_INPUT",
    }


def test_report_tools_return_invalid_input_envelope_for_bad_date_window(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    weekly = server._tool_manager.get_tool("finance_generate_weekly_report").fn

    result = weekly("2026-03-10", "2026-03-01")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_sensitive_query_tool_rejects_large_limit(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    sensitive = server._tool_manager.get_tool("sensitive_finance_query").fn

    assert sensitive(limit=0) == {
        "success": False,
        "data": None,
        "error": "limit must be between 1 and 500",
        "error_code": "INVALID_INPUT",
    }

    assert sensitive(limit=501) == {
        "success": False,
        "data": None,
        "error": "limit must be between 1 and 500",
        "error_code": "INVALID_INPUT",
    }


def test_tool_calls_close_thread_local_connection_after_use(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    safe_accounts = server._tool_manager.get_tool("safe_finance_accounts").fn

    safe_accounts()

    assert getattr(service._local, "conn", None) is None


def test_finance_import_tool_rejects_paths_outside_allowed_import_root(tmp_path):
    import_root = tmp_path / "staging"
    import_root.mkdir()
    outside_source = tmp_path / "free checking transactions.csv"
    outside_source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault", import_root=import_root)
    server = create_finance_server(service)
    finance_import = server._tool_manager.get_tool("finance_import").fn

    result = finance_import(str(outside_source), "DCU")

    assert result == {
        "success": False,
        "data": None,
        "error": "source_ref must be inside the allowed import root",
        "error_code": "INVALID_INPUT",
    }


def test_sensitive_finance_query_rejects_reversed_date_range(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    sensitive = server._tool_manager.get_tool("sensitive_finance_query").fn

    result = sensitive(start_date="2026-03-31", end_date="2026-03-01")

    assert result == {
        "success": False,
        "data": None,
        "error": "start_date must be on or before end_date",
        "error_code": "INVALID_INPUT",
    }


def test_sensitive_finance_query_rejects_blank_description_contains(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    sensitive = server._tool_manager.get_tool("sensitive_finance_query").fn

    result = sensitive(description_contains="   ")

    assert result == {
        "success": False,
        "data": None,
        "error": "description_contains must not be blank",
        "error_code": "INVALID_INPUT",
    }


def test_sensitive_finance_query_rejects_invalid_start_date_without_end_date(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    sensitive = server._tool_manager.get_tool("sensitive_finance_query").fn

    result = sensitive(start_date="2026-99-99")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_sensitive_finance_query_rejects_invalid_end_date_without_start_date(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    sensitive = server._tool_manager.get_tool("sensitive_finance_query").fn

    result = sensitive(end_date="2026-99-99")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_sensitive_finance_query_rejects_blank_scalar_filters(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    sensitive = server._tool_manager.get_tool("sensitive_finance_query").fn

    for kwargs in (
        {"category_name": "   "},
        {"merchant": "   "},
        {"account_name": "   "},
    ):
        result = sensitive(**kwargs)
        assert result["success"] is False
        assert result["error_code"] == "INVALID_INPUT"


def test_finance_query_tool_writes_audit_log_for_sum_spending(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")

    class _SumLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            return (
                '{"intent":"sum_spending","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31"},'
                '"confidence":0.9,"needs_clarification":false}'
            )

    server = create_finance_server(service, llm=_SumLLM())
    finance_query = server._tool_manager.get_tool("finance_query").fn

    _call_tool_sync(finance_query, "how much did I spend in March", "2026-03-31")

    row = service.conn.execute(
        "SELECT tool_name, summary FROM audit_log WHERE tool_name = 'finance_query'"
    ).fetchone()
    assert row is not None
    assert row["tool_name"] == "finance_query"
    assert "sum_spending" in row["summary"]


def test_finance_query_tool_writes_audit_log_for_count_transactions(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")

    class _CountLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            return (
                '{"intent":"count_transactions","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31"},'
                '"confidence":0.9,"needs_clarification":false}'
            )

    server = create_finance_server(service, llm=_CountLLM())
    finance_query = server._tool_manager.get_tool("finance_query").fn

    _call_tool_sync(finance_query, "how many transactions in March", "2026-03-31")

    row = service.conn.execute(
        "SELECT tool_name, summary FROM audit_log WHERE tool_name = 'finance_query'"
    ).fetchone()
    assert row is not None
    assert row["tool_name"] == "finance_query"
    assert "count_transactions" in row["summary"]


def test_finance_query_tool_writes_audit_log_for_list_transactions(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")

    class _ListLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            return (
                '{"intent":"list_transactions","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31"},'
                '"confidence":0.9,"needs_clarification":false}'
            )

    server = create_finance_server(service, llm=_ListLLM())
    finance_query = server._tool_manager.get_tool("finance_query").fn

    _call_tool_sync(finance_query, "show me march transactions", "2026-03-31", session_ref="s1")

    row = service.conn.execute("SELECT tool_name, session_ref FROM audit_log").fetchone()
    assert row is not None
    assert row["tool_name"] == "finance_query"
    assert row["session_ref"] == "s1"


def test_safe_finance_summary_returns_internal_error_envelope_for_unexpected_exception(
    tmp_path, monkeypatch, caplog
):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")
    server = create_finance_server(service)
    safe_summary = server._tool_manager.get_tool("safe_finance_summary").fn

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "safe_finance_summary", boom)
    caplog.set_level("ERROR")

    result = safe_summary()

    assert result == {
        "success": False,
        "data": None,
        "error": "Internal server error",
        "error_code": "INTERNAL_ERROR",
    }
    assert "boom" in caplog.text


def test_finance_query_tool_threads_session_ref_into_sum_spending_audit_log(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")

    class _SumLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            return (
                '{"intent":"sum_spending","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31"},'
                '"confidence":0.9,"needs_clarification":false}'
            )

    server = create_finance_server(service, llm=_SumLLM())
    finance_query = server._tool_manager.get_tool("finance_query").fn

    _call_tool_sync(
        finance_query,
        "how much did I spend in March",
        "2026-03-31",
        session_ref="test-session-123",
    )

    row = service.conn.execute(
        "SELECT session_ref FROM audit_log WHERE tool_name = 'finance_query'"
    ).fetchone()
    assert row is not None
    assert row["session_ref"] == "test-session-123"


def test_finance_query_tool_threads_session_ref_into_count_transactions_audit_log(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")

    class _CountLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            return (
                '{"intent":"count_transactions","filters":{"start_date":"2026-03-01",'
                '"end_date":"2026-03-31"},'
                '"confidence":0.9,"needs_clarification":false}'
            )

    server = create_finance_server(service, llm=_CountLLM())
    finance_query = server._tool_manager.get_tool("finance_query").fn

    _call_tool_sync(
        finance_query,
        "how many transactions in March",
        "2026-03-31",
        session_ref="test-session-456",
    )

    row = service.conn.execute(
        "SELECT session_ref FROM audit_log WHERE tool_name = 'finance_query'"
    ).fetchone()
    assert row is not None
    assert row["session_ref"] == "test-session-456"


def test_finance_query_tool_rejects_llm_reversed_date_range(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path / "vault")

    class _ReversedDateLLM:
        async def run_json_prompt(self, prompt: str) -> str:
            return (
                '{"intent":"sum_spending","filters":{"start_date":"2026-03-31",'
                '"end_date":"2026-03-01"},'
                '"confidence":0.9,"needs_clarification":false}'
            )

    server = create_finance_server(service, llm=_ReversedDateLLM())
    finance_query = server._tool_manager.get_tool("finance_query").fn

    result = _call_tool_sync(
        finance_query,
        "how much did I spend last month",
        "2026-03-31",
    )

    assert result == {
        "success": False,
        "data": None,
        "error": "start_date must be on or before end_date",
        "error_code": "INVALID_INPUT",
    }
