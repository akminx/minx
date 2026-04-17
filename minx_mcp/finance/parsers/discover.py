from __future__ import annotations

import re
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


def parse_discover_pdf(path: Path, account_name: str) -> ParsedImportBatch:
    if path.stat().st_size > MAX_FINANCE_IMPORT_FILE_BYTES:
        raise InvalidInputError(
            f"Discover PDF exceeds maximum allowed size ({MAX_FINANCE_IMPORT_FILE_BYTES} bytes)"
        )
    text = extract_text(path)
    if len(text.encode("utf-8")) > MAX_FINANCE_IMPORT_FILE_BYTES:
        raise InvalidInputError(
            f"Extracted Discover PDF text exceeds maximum allowed size ({MAX_FINANCE_IMPORT_FILE_BYTES} bytes)"
        )
    transactions: list[ParsedTransaction] = []
    for line in text.splitlines():
        match = re.match(
            (
                r"^(?P<trans>\d{2}/\d{2}/\d{2,4})\s+\d{2}/\d{2}/\d{2,4}\s+"
                r"(?P<desc>.+?)\s+\$\s*(?P<amount>\d+\.\d{2})\s+(?P<category>.+)$"
            ),
            line.strip(),
        )
        if not match:
            continue
        if len(transactions) >= MAX_FINANCE_IMPORT_ROWS:
            raise InvalidInputError(
                f"Discover PDF exceeds maximum row count ({MAX_FINANCE_IMPORT_ROWS} transactions)"
            )
        month, day, year = match.group("trans").split("/")
        description = match.group("desc")
        transactions.append(
            ParsedTransaction(
                posted_at=f"{year if len(year) == 4 else f'20{year}'}-{month}-{day}",
                description=description,
                amount_cents=-parse_dollars_to_cents(match.group("amount")),
                merchant=description,
                category_hint=match.group("category").lower(),
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
