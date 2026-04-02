from pathlib import Path

from minx_mcp.finance.service import FinanceService


def test_weekly_report_includes_required_sections(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-02-24,H-E-B,Withdrawal,-20.00\n"
        "2026-02-25,CAFE,Withdrawal,-10.00\n"
        "2026-03-02,H-E-B,Withdrawal,-45.20\n"
        "2026-03-03,Payroll,Deposit,1200.00\n"
        "2026-03-04,CAFE,Withdrawal,-30.00\n"
        "2026-03-05,Unknown Merchant,Withdrawal,-500.00\n"
    )
    vault_root = tmp_path / "vault"
    service = FinanceService(tmp_path / "minx.db", vault_root)
    service.finance_import(str(source), account_name="DCU")
    service.add_category_rule("Groceries", "merchant_contains", "H-E-B")
    service.add_category_rule("Dining Out", "merchant_contains", "CAFE")
    service.apply_category_rules()

    weekly = service.generate_weekly_report("2026-03-02", "2026-03-08")
    summary = weekly["summary"]
    report_text = Path(weekly["vault_path"]).read_text()

    assert summary["totals"] == {"inflow": 1200.0, "outflow": 575.2}
    assert any(item["category_name"] == "Groceries" for item in summary["top_categories"])
    assert any(item["merchant"] == "Unknown Merchant" for item in summary["notable_merchants"])
    assert any(item["category_name"] == "Groceries" for item in summary["category_changes"])
    assert any(item["kind"] == "large_uncategorized" for item in summary["anomalies"])
    assert any(
        item["description"] == "Unknown Merchant"
        for item in summary["uncategorized_transactions"]
    )
    assert "## Totals" in report_text
    assert "## Top Categories" in report_text
    assert "## Notable Merchants" in report_text
    assert "## Category Changes Vs Prior Week" in report_text
    assert "## Anomalies" in report_text
    assert "## Uncategorized Transactions" in report_text


def test_monthly_report_includes_required_sections(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-02-10,NETFLIX,Withdrawal,-15.00\n"
        "2026-02-24,H-E-B,Withdrawal,-20.00\n"
        "2026-03-02,H-E-B,Withdrawal,-45.20\n"
        "2026-03-03,Payroll,Deposit,1200.00\n"
        "2026-03-05,Unknown Merchant,Withdrawal,-500.00\n"
        "2026-03-10,NETFLIX,Withdrawal,-15.00\n"
        "2026-03-12,NEW SHOP,Withdrawal,-20.00\n"
    )
    vault_root = tmp_path / "vault"
    service = FinanceService(tmp_path / "minx.db", vault_root)
    service.finance_import(str(source), account_name="DCU")
    service.add_category_rule("Groceries", "merchant_contains", "H-E-B")
    service.add_category_rule("Subscriptions", "merchant_contains", "NETFLIX")
    service.apply_category_rules()

    monthly = service.generate_monthly_report("2026-03-01", "2026-03-31")
    summary = monthly["summary"]
    report_text = Path(monthly["vault_path"]).read_text()

    assert any(item["account_name"] == "DCU" for item in summary["account_rollups"])
    assert any(item["category_name"] == "Groceries" for item in summary["category_totals"])
    assert any(item["account_name"] == "DCU" for item in summary["changes_vs_prior_month"])
    assert any(
        item["merchant"] == "NETFLIX" for item in summary["recurring_charge_highlights"]
    )
    assert any(item["kind"] == "large_uncategorized" for item in summary["anomalies"])
    assert any(
        item["kind"] == "new_merchant" and item["merchant"] == "NEW SHOP"
        for item in summary["uncategorized_or_new_merchants"]
    )
    assert "## Account Rollups" in report_text
    assert "## Category Totals" in report_text
    assert "## Changes Vs Prior Month" in report_text
    assert "## Recurring Charge Highlights" in report_text
    assert "## Anomalies" in report_text
    assert "## Uncategorized Or Newly Seen Merchants" in report_text
