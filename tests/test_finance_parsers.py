from pathlib import Path

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.finance.importers import detect_source_kind, parse_source_file
from minx_mcp.finance.parsers.dcu import parse_dcu_csv
from minx_mcp.finance.parsers.generic_csv import parse_generic_csv


def test_detect_robinhood_csv(tmp_path):
    path = tmp_path / "robinhood_transactions.csv"
    path.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-01,09:00,Alex,1234,-12.50,COFFEE\n"
    )
    assert detect_source_kind(path) == "robinhood_csv"


def test_parse_dcu_csv(tmp_path):
    path = tmp_path / "free checking transactions.csv"
    path.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-01,Payroll,Deposit,1200.00\n"
        "2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    parsed = parse_source_file(path, account_name="DCU")
    assert parsed["account_name"] == "DCU"
    assert parsed["transactions"][1]["merchant"] == "H-E-B"


def test_parse_discover_pdf_via_liteparse_adapter(tmp_path, monkeypatch):
    path = tmp_path / "discover_statement.pdf"
    path.write_text("stub")
    sample = "Transactions\n03/01/26 03/01/26 H-E-B $ 42.16 Supermarkets\n"
    monkeypatch.setattr("minx_mcp.finance.parsers.discover.extract_text", lambda _: sample)
    parsed = parse_source_file(path, account_name="Discover", source_kind="discover_pdf")
    assert parsed["transactions"][0]["amount_cents"] == -4216


def test_parse_generic_csv_with_saved_mapping(tmp_path):
    path = tmp_path / "generic.csv"
    path.write_text("Booked,Debit,Merchant,Details\n2026-03-01,18.10,TARGET,Household\n")
    mapping = {
        "date_column": "Booked",
        "amount_column": "Debit",
        "merchant_column": "Merchant",
        "description_column": "Details",
        "date_format": "%Y-%m-%d",
    }
    parsed = parse_source_file(
        path,
        account_name="Discover",
        source_kind="generic_csv",
        mapping=mapping,
    )
    assert parsed["transactions"][0]["description"] == "Household"


def test_parse_dcu_csv_returns_amount_cents(tmp_path):
    source = tmp_path / "dcu.csv"
    source.write_text("Date,Description,Amount\n2026-03-28,HEB,-42.16\n")

    parsed = parse_dcu_csv(source, "DCU")

    assert parsed["transactions"][0]["amount_cents"] == -4216
    assert "amount" not in parsed["transactions"][0]


def test_generic_csv_rejects_more_than_two_decimals(tmp_path):
    source = tmp_path / "generic.csv"
    source.write_text("posted,description,amount\n03/28/2026,HEB,-12.345\n")

    with pytest.raises(InvalidInputError, match="at most 2 decimal places"):
        parse_generic_csv(
            source,
            "DCU",
            {
                "date_column": "posted",
                "date_format": "%m/%d/%Y",
                "description_column": "description",
                "amount_column": "amount",
            },
        )
