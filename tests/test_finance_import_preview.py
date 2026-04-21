from minx_mcp.finance.service import FinanceService


def test_import_workflow_preview_returns_decimal_string_not_float(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path, import_root=tmp_path)
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )

    result = service.finance_import_preview(str(source), "DCU")
    amount = result["preview"]["sample_transactions"][0]["amount"]
    assert isinstance(amount, str)
    assert amount == "-45.20"
    assert not isinstance(amount, float)


def test_finance_import_preview_returns_detected_mapping_and_sample(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path, import_root=tmp_path)
    source = tmp_path / "free checking transactions.csv"
    source.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )

    result = service.finance_import_preview(str(source), "DCU")

    assert result["preview"]["result_type"] == "preview"
    assert result["preview"]["source_kind"] == "dcu_csv"
    assert result["preview"]["sample_transactions"][0]["description"] == "H-E-B"


def test_finance_import_preview_reflects_canonical_and_raw_merchant_values(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path, import_root=tmp_path)
    source = tmp_path / "robinhood_transactions.csv"
    source.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,SQ *JOES CAFE 1234\n"
    )

    result = service.finance_import_preview(str(source), "Robinhood Gold")

    sample = result["preview"]["sample_transactions"][0]
    assert sample["merchant"] == "Joe's Cafe"
    assert sample["raw_merchant"] == "SQ *JOES CAFE 1234"


def test_finance_import_preview_rejects_paths_outside_import_root(tmp_path):
    import_root = tmp_path / "imports"
    import_root.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text(
        "Date,Description,Transaction Type,Amount\n2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    service = FinanceService(tmp_path / "minx.db", tmp_path, import_root=import_root)

    try:
        service.finance_import_preview(str(outside), "DCU")
    except Exception as exc:
        assert str(exc) == "source_ref must be inside the allowed import root"
    else:
        raise AssertionError("expected preview to reject sources outside import_root")


def test_finance_import_preview_reports_mapping_clarify_for_unknown_generic_csv(tmp_path):
    service = FinanceService(tmp_path / "minx.db", tmp_path, import_root=tmp_path)
    source = tmp_path / "transactions.csv"
    source.write_text("posted,details,amount\n03/02/2026,Coffee,-12.50\n")

    result = service.finance_import_preview(str(source), "DCU", source_kind="generic_csv")

    assert result["preview"]["result_type"] == "clarify"
    assert result["preview"]["reason"] == "missing_mapping"
