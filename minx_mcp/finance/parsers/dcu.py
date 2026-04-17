from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from minx_mcp.contracts import InvalidInputError
from minx_mcp.document_text import extract_text
from minx_mcp.finance.import_models import (
    MAX_FINANCE_IMPORT_FILE_BYTES,
    MAX_FINANCE_IMPORT_ROWS,
    ParsedImportBatch,
    ParsedTransaction,
)
from minx_mcp.money import parse_dollars_to_cents


def _parse_dcu_posted_at(raw: str) -> str:
    """Normalize DCU statement ``Date`` to ISO ``YYYY-MM-DD``.

    DCU US exports are assumed to use month-first dates (``MM/DD/YYYY``). ``YYYY-MM-DD``
    is also accepted so ISO-shaped fixtures and exports remain valid.
    """
    s = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise InvalidInputError(f"DCU CSV has invalid or unsupported Date value: {raw!r}")


def parse_dcu_csv(path: Path, account_name: str) -> ParsedImportBatch:
    if path.stat().st_size > MAX_FINANCE_IMPORT_FILE_BYTES:
        raise InvalidInputError(
            f"DCU CSV exceeds maximum allowed size ({MAX_FINANCE_IMPORT_FILE_BYTES} bytes)"
        )
    transactions: list[ParsedTransaction] = []
    with path.open(newline="") as handle:
        for row_num, row in enumerate(csv.DictReader(handle), start=1):
            if row_num > MAX_FINANCE_IMPORT_ROWS:
                raise InvalidInputError(
                    f"DCU CSV exceeds maximum row count ({MAX_FINANCE_IMPORT_ROWS} data rows)"
                )
            try:
                description = row["Description"]
                raw_date = row["Date"]
                raw_amount = row["Amount"]
            except KeyError as exc:
                raise InvalidInputError(
                    f"DCU CSV is missing expected column {exc.args[0]!r}"
                ) from exc
            posted_at = _parse_dcu_posted_at(raw_date)
            transactions.append(
                ParsedTransaction(
                    posted_at=posted_at,
                    description=description,
                    amount_cents=parse_dollars_to_cents(raw_amount),
                    merchant=description,
                    category_hint=None,
                    external_id=None,
                )
            )
    return ParsedImportBatch(
        account_name=account_name,
        source_type="csv",
        source_ref=str(path),
        raw_fingerprint="",
        transactions=transactions,
    )


def parse_dcu_pdf(path: Path, account_name: str) -> ParsedImportBatch:
    if path.stat().st_size > MAX_FINANCE_IMPORT_FILE_BYTES:
        raise InvalidInputError(
            f"DCU PDF exceeds maximum allowed size ({MAX_FINANCE_IMPORT_FILE_BYTES} bytes)"
        )
    text = extract_text(path)
    if len(text.encode("utf-8")) > MAX_FINANCE_IMPORT_FILE_BYTES:
        raise InvalidInputError(
            f"Extracted DCU PDF text exceeds maximum allowed size ({MAX_FINANCE_IMPORT_FILE_BYTES} bytes)"
        )
    transactions: list[ParsedTransaction] = []
    for line in text.splitlines():
        match = re.match(
            r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<desc>.+?)\s+(?P<amount>-?\d+\.\d{2})$",
            line.strip(),
        )
        if not match:
            continue
        if len(transactions) >= MAX_FINANCE_IMPORT_ROWS:
            raise InvalidInputError(
                f"DCU PDF exceeds maximum row count ({MAX_FINANCE_IMPORT_ROWS} transactions)"
            )
        description = match.group("desc")
        transactions.append(
            ParsedTransaction(
                posted_at=match.group("date"),
                description=description,
                amount_cents=parse_dollars_to_cents(match.group("amount")),
                merchant=description,
                category_hint=None,
                external_id=None,
            )
        )
    return ParsedImportBatch(
        account_name=account_name,
        source_type="pdf",
        source_ref=str(path),
        raw_fingerprint="",
        transactions=transactions,
    )
