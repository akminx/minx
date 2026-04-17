from __future__ import annotations

import csv
import re
from pathlib import Path

from minx_mcp.contracts import InvalidInputError
from minx_mcp.document_text import extract_text
from minx_mcp.finance.import_models import ParsedImportBatch, ParsedTransaction
from minx_mcp.money import parse_dollars_to_cents


def parse_dcu_csv(path: Path, account_name: str) -> ParsedImportBatch:
    transactions: list[ParsedTransaction] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                description = row["Description"]
                posted_at = row["Date"]
                raw_amount = row["Amount"]
            except KeyError as exc:
                raise InvalidInputError(
                    f"DCU CSV is missing expected column {exc.args[0]!r}"
                ) from exc
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
    text = extract_text(path)
    transactions: list[ParsedTransaction] = []
    for line in text.splitlines():
        match = re.match(
            r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<desc>.+?)\s+(?P<amount>-?\d+\.\d{2})$",
            line.strip(),
        )
        if not match:
            continue
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
