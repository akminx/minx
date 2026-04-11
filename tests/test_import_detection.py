from __future__ import annotations

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.interpretation.import_detection import (
    _detect_from_filename,
    _detect_from_tabular_text,
)
from pathlib import Path


# ---------------------------------------------------------------------------
# _detect_from_filename
# ---------------------------------------------------------------------------

def test_detect_from_filename_robinhood_csv():
    path = Path("downloads/robinhood_transactions.csv")
    assert _detect_from_filename(path) == "robinhood_csv"


def test_detect_from_filename_dcu_csv():
    path = Path("downloads/free checking transactions.csv")
    assert _detect_from_filename(path) == "dcu_csv"


def test_detect_from_filename_discover_pdf():
    path = Path("downloads/Discover-Statement-2026-03.pdf")
    assert _detect_from_filename(path) == "discover_pdf"


def test_detect_from_filename_unknown_returns_none():
    path = Path("downloads/mystery_bank.csv")
    assert _detect_from_filename(path) is None


# ---------------------------------------------------------------------------
# _detect_from_tabular_text (via tmp files)
# ---------------------------------------------------------------------------

def test_detect_from_tabular_text_robinhood_columns(tmp_path):
    csv_path = tmp_path / "transactions.csv"
    csv_path.write_text(
        "Date,Time,Cardholder,Card,Amount,Description\n"
        "2026-03-15,10:00,John Doe,1234,-15.00,Coffee\n"
    )

    result = _detect_from_tabular_text(csv_path)

    assert result == "robinhood_csv"


def test_detect_from_tabular_text_dcu_columns(tmp_path):
    csv_path = tmp_path / "transactions.csv"
    csv_path.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-15,Coffee Shop,Debit,-15.00\n"
    )

    result = _detect_from_tabular_text(csv_path)

    assert result == "dcu_csv"


def test_detect_from_tabular_text_generic_csv(tmp_path):
    csv_path = tmp_path / "transactions.csv"
    csv_path.write_text(
        "Field1,Field2,Field3\n"
        "value1,value2,value3\n"
    )

    result = _detect_from_tabular_text(csv_path)

    assert result == "generic_csv"


def test_detect_from_tabular_text_single_column_raises(tmp_path):
    csv_path = tmp_path / "transactions.csv"
    csv_path.write_text("OnlyOneColumn\nvalue1\n")

    with pytest.raises(InvalidInputError, match="Could not detect"):
        _detect_from_tabular_text(csv_path)


def test_detect_from_tabular_text_empty_file_raises(tmp_path):
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("")

    with pytest.raises(InvalidInputError, match="file was empty"):
        _detect_from_tabular_text(csv_path)
