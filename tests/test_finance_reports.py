from minx_mcp.db import get_connection
from minx_mcp.finance.service import FinanceService


def test_weekly_and_monthly_reports_write_to_vault(tmp_path):
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-02,H-E-B,Withdrawal,-45.20\n"
        "2026-03-10,Payroll,Deposit,1200.00\n"
    )
    vault_root = tmp_path / "vault"
    service = FinanceService(get_connection(tmp_path / "minx.db"), vault_root)
    service.finance_import(str(source), account_name="DCU")
    weekly = service.generate_weekly_report("2026-03-02", "2026-03-08")
    monthly = service.generate_monthly_report("2026-03-01", "2026-03-31")
    assert weekly["vault_path"].endswith("Finance/weekly-2026-03-02.md")
    assert monthly["vault_path"].endswith("Finance/monthly-2026-03.md")
