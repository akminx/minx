from minx_mcp.finance.service import FinanceService


def test_import_to_summary_to_report_flow(tmp_path):
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    vault = tmp_path / "vault"
    service = FinanceService(tmp_path / "minx.db", vault)
    imported = service.finance_import(str(source), account_name="Robinhood Gold")
    summary = service.safe_finance_summary()
    report = service.generate_monthly_report("2026-03-01", "2026-03-31")
    assert imported["result"]["inserted"] == 1
    assert summary["categories"]
    assert isinstance(summary["net_total"], float)
    assert summary["net_total"] == -12.5
    assert report["summary"]["account_rollups"][0]["total_amount"] == -12.5
    assert report["vault_path"].endswith("Finance/monthly-2026-03.md")
