from __future__ import annotations

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


def _parse_discover_posted_at(raw: str) -> str:
    s = raw.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise InvalidInputError(f"Discover PDF: invalid date {raw!r}")


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
        description = match.group("desc")
        posted_at = _parse_discover_posted_at(match.group("trans"))
        transactions.append(
            ParsedTransaction(
                posted_at=posted_at,
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
