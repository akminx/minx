from __future__ import annotations

import csv
import re
from pathlib import Path

from minx_mcp.document_text import extract_text
from minx_mcp.money import parse_dollars_to_cents


def parse_dcu_csv(path: Path, account_name: str) -> dict[str, object]:
    transactions: list[dict[str, object | None]] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            description = row["Description"]
            transactions.append(
                {
                    "posted_at": row["Date"],
                    "description": description,
                    "amount_cents": parse_dollars_to_cents(row["Amount"]),
                    "merchant": description,
                    "category_hint": None,
                    "external_id": None,
                }
            )
    return {
        "account_name": account_name,
        "source_type": "csv",
        "source_ref": str(path),
        "raw_fingerprint": "",
        "transactions": transactions,
    }


def parse_dcu_pdf(path: Path, account_name: str) -> dict[str, object]:
    text = extract_text(path)
    transactions: list[dict[str, object | None]] = []
    for line in text.splitlines():
        match = re.match(
            r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<desc>.+?)\s+(?P<amount>-?\d+\.\d{2})$",
            line.strip(),
        )
        if not match:
            continue
        description = match.group("desc")
        transactions.append(
            {
                "posted_at": match.group("date"),
                "description": description,
                "amount_cents": parse_dollars_to_cents(match.group("amount")),
                "merchant": description,
                "category_hint": None,
                "external_id": None,
            }
        )
    return {
        "account_name": account_name,
        "source_type": "pdf",
        "source_ref": str(path),
        "raw_fingerprint": "",
        "transactions": transactions,
    }
