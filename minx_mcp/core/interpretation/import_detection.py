from __future__ import annotations

import re
from pathlib import Path

from minx_mcp.contracts import InvalidInputError
from minx_mcp import document_text

_PDF_DISCOVER_LINE = re.compile(
    (
        r"^(?P<trans>\d{2}/\d{2}/\d{2})\s+\d{2}/\d{2}/\d{2}\s+"
        r"(?P<desc>.+?)\s+\$\s*(?P<amount>\d+\.\d{2})\s+(?P<category>.+)$"
    )
)
_PDF_DCU_LINE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<desc>.+?)\s+(?P<amount>-?\d+\.\d{2})$"
)


def detect_finance_source_kind(path: Path) -> str:
    filename_kind = _detect_from_filename(path)
    if filename_kind is not None:
        return filename_kind

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _detect_from_pdf_text(path)
    return _detect_from_tabular_text(path)


def _detect_from_filename(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith("robinhood_transactions.csv"):
        return "robinhood_csv"
    if "free checking transactions.csv" in name:
        return "dcu_csv"
    if "discover" in name and path.suffix.lower() == ".pdf":
        return "discover_pdf"
    if name.startswith("stmt_") and path.suffix.lower() == ".pdf":
        return "dcu_pdf"
    return None


def _detect_from_pdf_text(path: Path) -> str:
    try:
        text = document_text.extract_text(path)
    except Exception as exc:  # pragma: no cover - exercised through failure paths
        raise InvalidInputError(
            f"Could not inspect PDF contents for {path.name}: {exc}"
        ) from exc

    for line in _nonempty_lines(text):
        if _PDF_DISCOVER_LINE.match(line):
            return "discover_pdf"
        if _PDF_DCU_LINE.match(line):
            return "dcu_pdf"

    preview = _preview_text(text)
    raise InvalidInputError(
        f"Could not detect finance source for {path}: sampled text {preview}"
    )


def _detect_from_tabular_text(path: Path) -> str:
    text = _read_text(path)
    lines = _nonempty_lines(text)
    if not lines:
        raise InvalidInputError(f"Could not detect finance source for {path}: file was empty")

    columns = _parse_header_columns(lines[0])
    if columns is None:
        preview = _preview_text(lines[0])
        raise InvalidInputError(
            f"Could not detect finance source for {path}: sampled structure {preview}"
        )

    normalized_columns = {column.casefold() for column in columns}
    if {
        "date",
        "time",
        "cardholder",
        "card",
        "amount",
        "description",
    }.issubset(normalized_columns):
        return "robinhood_csv"

    if {
        "date",
        "description",
        "transaction type",
        "amount",
    }.issubset(normalized_columns):
        return "dcu_csv"

    if len(columns) >= 2:
        return "generic_csv"

    preview = _preview_columns(columns) if columns else _preview_text(lines[0])
    raise InvalidInputError(
        f"Could not detect finance source for {path}: sampled columns {preview}"
    )


def _read_text(path: Path) -> str:
    return path.read_text(errors="replace")


def _nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _parse_header_columns(line: str) -> list[str] | None:
    for delimiter in (",", "\t", "|", ";"):
        if delimiter in line:
            return [column.strip() for column in line.split(delimiter)]
    return None


def _preview_columns(columns: list[str]) -> str:
    return ", ".join(columns[:10]) if columns else "<none>"


def _preview_text(text: str) -> str:
    flattened = " | ".join(_nonempty_lines(text)[:3])
    if not flattened:
        flattened = "<empty>"
    return flattened[:200]
