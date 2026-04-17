from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from minx_mcp.contracts import InvalidInputError
from minx_mcp.finance.import_models import (
    MAX_FINANCE_IMPORT_FILE_BYTES,
    MAX_FINANCE_IMPORT_ROWS,
    ParsedImportBatch,
    ParsedTransaction,
)
from minx_mcp.money import parse_dollars_to_cents


def _parse_robinhood_posted_at(raw: str) -> str:
    s = raw.strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise InvalidInputError(f"Robinhood Gold CSV: invalid date {raw!r}") from exc


def parse_robinhood_csv(path: Path, account_name: str) -> ParsedImportBatch:
    if path.stat().st_size > MAX_FINANCE_IMPORT_FILE_BYTES:
        raise InvalidInputError(
            f"Robinhood CSV exceeds maximum allowed size ({MAX_FINANCE_IMPORT_FILE_BYTES} bytes)"
        )
    transactions: list[ParsedTransaction] = []
    with path.open(newline="") as handle:
        for row_num, row in enumerate(csv.DictReader(handle), start=1):
            if row_num > MAX_FINANCE_IMPORT_ROWS:
                raise InvalidInputError(
                    f"Robinhood CSV exceeds maximum row count ({MAX_FINANCE_IMPORT_ROWS} data rows)"
                )
            try:
                description = row["Description"]
                raw_date = row["Date"]
                raw_amount = row["Amount"]
            except KeyError as exc:
                raise InvalidInputError(
                    f"Robinhood CSV is missing expected column {exc.args[0]!r}"
                ) from exc
            posted_at = _parse_robinhood_posted_at(raw_date)
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
