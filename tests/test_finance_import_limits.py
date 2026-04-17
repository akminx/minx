import hashlib

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.finance import importers
from minx_mcp.finance.importers import stream_snapshot_copy_and_hash
from minx_mcp.finance.parsers.generic_csv import parse_generic_csv


def test_generic_csv_rejects_file_exceeding_max_bytes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "minx_mcp.finance.parsers.generic_csv.MAX_FINANCE_IMPORT_FILE_BYTES",
        50,
    )
    path = tmp_path / "big.csv"
    path.write_bytes(b"x" * 60)
    with pytest.raises(InvalidInputError, match="maximum allowed size"):
        parse_generic_csv(
            path,
            "DCU",
            {
                "date_column": "d",
                "amount_column": "a",
                "description_column": "m",
                "date_format": "%Y-%m-%d",
            },
        )


def test_generic_csv_rejects_file_exceeding_max_rows(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "minx_mcp.finance.parsers.generic_csv.MAX_FINANCE_IMPORT_ROWS",
        2,
    )
    lines = ["d,a,m", "2026-01-01,1.00,one", "2026-01-02,2.00,two", "2026-01-03,3.00,three"]
    path = tmp_path / "many.csv"
    path.write_text("\n".join(lines))
    with pytest.raises(InvalidInputError, match="maximum row count"):
        parse_generic_csv(
            path,
            "DCU",
            {
                "date_column": "d",
                "amount_column": "a",
                "description_column": "m",
                "date_format": "%Y-%m-%d",
            },
        )


def test_stream_snapshot_copy_aborts_on_oversized_source(tmp_path, monkeypatch) -> None:
    chunk = importers._READ_CHUNK
    monkeypatch.setattr(
        "minx_mcp.finance.importers.MAX_FINANCE_IMPORT_FILE_BYTES",
        chunk + 10,
    )
    payload = b"z" * (chunk + chunk)
    source = tmp_path / "src.bin"
    dest = tmp_path / "dst.bin"
    source.write_bytes(payload)

    with pytest.raises(InvalidInputError, match="maximum allowed size"):
        stream_snapshot_copy_and_hash(source, dest)

    assert not dest.exists()

    # Sanity: small file still hashes and copies normally under the patched limit
    small = tmp_path / "small.bin"
    out = tmp_path / "out.bin"
    small.write_bytes(b"abc")
    assert stream_snapshot_copy_and_hash(small, out) == hashlib.sha256(b"abc").hexdigest()
    assert out.read_bytes() == b"abc"
