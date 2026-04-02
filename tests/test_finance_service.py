from minx_mcp.db import get_connection
from minx_mcp.finance.service import FinanceService


def test_import_job_is_idempotent_for_same_file(tmp_path):
    db_path = tmp_path / "minx.db"
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    service = FinanceService(get_connection(db_path), tmp_path)
    first = service.finance_import(str(source), account_name="Robinhood Gold")
    second = service.finance_import(str(source), account_name="Robinhood Gold")
    assert first["job_id"] == second["job_id"]


def test_manual_and_rule_based_categorization_both_work(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path)
    service.finance_import(str(source), account_name="DCU")
    service.add_category_rule("Groceries", "merchant_contains", "H-E-B")
    service.apply_category_rules()
    tx = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert tx["category_name"] == "Groceries"
    service.finance_categorize([tx["id"]], "Dining Out")
    changed = service.sensitive_finance_query(limit=1)["transactions"][0]
    assert changed["category_name"] == "Dining Out"


def test_safe_summary_and_sensitive_query_are_separate(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text("Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n")
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path)
    service.finance_import(str(source), account_name="DCU")
    safe = service.safe_finance_summary()
    sensitive = service.sensitive_finance_query(limit=10, session_ref="abc-123")
    assert "transactions" not in safe
    assert sensitive["transactions"][0]["description"] == "H-E-B"


def test_anomalies_flag_large_uncategorized_transactions(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-02,Unknown Merchant,Withdrawal,-500.00\n"
    )
    service = FinanceService(get_connection(tmp_path / "minx.db"), tmp_path)
    service.finance_import(str(source), account_name="DCU")
    anomalies = service.finance_anomalies()
    assert anomalies["items"][0]["kind"] == "large_uncategorized"
