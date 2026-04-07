from __future__ import annotations

import csv
from pathlib import Path

from minx_mcp.finance.import_models import ParsedImportBatch, ParsedTransaction
from minx_mcp.money import parse_dollars_to_cents


def parse_robinhood_csv(path: Path, account_name: str) -> ParsedImportBatch:
    transactions: list[ParsedTransaction] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            description = row["Description"]
            transactions.append(
                ParsedTransaction(
                    posted_at=row["Date"],
                    description=description,
                    amount_cents=parse_dollars_to_cents(row["Amount"]),
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
