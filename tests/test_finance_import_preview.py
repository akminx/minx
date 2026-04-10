from minx_mcp.finance.service import FinanceService


def test_finance_import_preview_returns_detected_mapping_and_sample(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path, import_root=tmp_path)
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )

    result = service.finance_import_preview(str(source), "DCU")

    assert result["preview"]["result_type"] == "preview"
    assert result["preview"]["source_kind"] == "dcu_csv"
    assert result["preview"]["sample_transactions"][0]["description"] == "H-E-B"


def test_finance_import_preview_reports_mapping_clarify_for_unknown_generic_csv(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path, import_root=tmp_path)
    source = tmp_path / "transactions.csv"
    source.write_text(
        "posted,details,amount\n"
        "03/02/2026,Coffee,-12.50\n"
    )

    result = service.finance_import_preview(str(source), "DCU", source_kind="generic_csv")

    assert result["preview"]["result_type"] == "clarify"
    assert result["preview"]["reason"] == "missing_mapping"
