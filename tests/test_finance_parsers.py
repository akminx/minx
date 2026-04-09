import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.finance import importers
from minx_mcp.finance.import_models import ParsedImportBatch
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


def test_detect_dcu_csv_from_content_when_filename_is_unhelpful(tmp_path):
    path = tmp_path / "statement.csv"
    path.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-01,Payroll,Deposit,1200.00\n"
    )

    assert detect_source_kind(path) == "dcu_csv"


def test_detect_discover_pdf_from_sampled_text_when_filename_is_unhelpful(tmp_path, monkeypatch):
    path = tmp_path / "statement.pdf"
    path.write_text("stub")
    monkeypatch.setattr(
        "minx_mcp.document_text.extract_text",
        lambda _: "Transactions\n03/01/26 03/01/26 H-E-B $ 42.16 Supermarkets\n",
    )

    assert detect_source_kind(path) == "discover_pdf"


def test_detect_unknown_csv_reports_sampled_structure(tmp_path):
    path = tmp_path / "statement.csv"
    path.write_text("not a finance export\njust some notes\n")

    with pytest.raises(InvalidInputError, match="not a finance export"):
        detect_source_kind(path)


def test_stream_snapshot_copy_and_hash_reads_in_chunks(tmp_path, monkeypatch):
    chunk_size = importers._READ_CHUNK
    payload = b"x" * chunk_size + b"y" * chunk_size + b"tail"
    source = tmp_path / "source.bin"
    dest = tmp_path / "snapshot.bin"
    source.write_bytes(payload)

    requests: list[int] = []
    returns: list[int] = []
    real_open = Path.open

    class RecordingReader:
        def __init__(self, data: bytes) -> None:
            self._data = data
            self._offset = 0

        def __enter__(self) -> "RecordingReader":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self, size: int = -1) -> bytes:
            requests.append(size)
            if size <= 0:
                raise AssertionError("expected fixed-size chunk reads")
            start = self._offset
            end = min(start + size, len(self._data))
            self._offset = end
            chunk = self._data[start:end]
            returns.append(len(chunk))
            return chunk

    def patched_open(self: Path, mode: str = "r", *args, **kwargs):
        if self == source and mode == "rb":
            return RecordingReader(payload)
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", patched_open)

    content_hash = importers.stream_snapshot_copy_and_hash(source, dest)

    assert requests == [chunk_size, chunk_size, chunk_size, chunk_size]
    assert returns == [chunk_size, chunk_size, 4, 0]
    assert dest.read_bytes() == payload
    assert content_hash == hashlib.sha256(payload).hexdigest()


def test_parse_source_file_streams_without_read_bytes_on_source(tmp_path):
    path = tmp_path / "free checking transactions.csv"
    path.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-01,Payroll,Deposit,1200.00\n"
        "2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    real_read_bytes = Path.read_bytes

    def guarded_read_bytes(self: Path) -> bytes:
        try:
            if self.resolve() == path.resolve():
                raise AssertionError("parse_source_file must stream the source, not read_bytes()")
        except OSError:
            pass
        return real_read_bytes(self)

    with patch.object(Path, "read_bytes", guarded_read_bytes):
        parsed = parse_source_file(path, account_name="DCU")
    assert parsed.transactions[1].merchant == "H-E-B"


def test_parse_dcu_csv(tmp_path):
    path = tmp_path / "free checking transactions.csv"
    path.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-01,Payroll,Deposit,1200.00\n"
        "2026-03-02,H-E-B,Withdrawal,-45.20\n"
    )
    parsed = parse_source_file(path, account_name="DCU")
    assert isinstance(parsed, ParsedImportBatch)
    assert parsed.account_name == "DCU"
    assert parsed.transactions[1].merchant == "H-E-B"


def test_parse_discover_pdf_via_liteparse_adapter(tmp_path, monkeypatch):
    path = tmp_path / "discover_statement.pdf"
    path.write_text("stub")
    sample = "Transactions\n03/01/26 03/01/26 H-E-B $ 42.16 Supermarkets\n"
    monkeypatch.setattr("minx_mcp.finance.parsers.discover.extract_text", lambda _: sample)
    parsed = parse_source_file(path, account_name="Discover", source_kind="discover_pdf")
    assert parsed.transactions[0].amount_cents == -4216


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
    assert parsed.transactions[0].description == "Household"


def test_parse_source_file_uses_content_detection_for_unhelpful_filename(tmp_path):
    path = tmp_path / "statement.csv"
    path.write_text(
        "Date,Description,Transaction Type,Amount\n"
        "2026-03-01,Payroll,Deposit,1200.00\n"
    )

    parsed = parse_source_file(path, account_name="DCU")

    assert parsed.transactions[0].description == "Payroll"


def test_parse_generic_csv_preserves_positive_amounts(tmp_path):
    source = tmp_path / "generic.csv"
    source.write_text("posted,description,amount\n03/28/2026,Payroll,1200.00\n")

    parsed = parse_generic_csv(
        source,
        "DCU",
        {
            "date_column": "posted",
            "date_format": "%m/%d/%Y",
            "description_column": "description",
            "amount_column": "amount",
        },
    )

    assert parsed.transactions[0].amount_cents == 120000


def test_parse_dcu_csv_returns_amount_cents(tmp_path):
    source = tmp_path / "dcu.csv"
    source.write_text("Date,Description,Amount\n2026-03-28,HEB,-42.16\n")

    parsed = parse_dcu_csv(source, "DCU")

    assert isinstance(parsed, ParsedImportBatch)
    assert parsed.transactions[0].amount_cents == -4216
    assert not hasattr(parsed.transactions[0], "amount")


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


def test_parse_generic_csv_requires_complete_mapping(tmp_path):
    source = tmp_path / "generic.csv"
    source.write_text("posted,description,amount\n03/28/2026,HEB,-12.34\n")

    with pytest.raises(InvalidInputError, match="generic csv mapping is missing required field"):
        parse_generic_csv(
            source,
            "DCU",
            {
                "date_column": "posted",
                "description_column": "description",
                "amount_column": "amount",
            },
        )
