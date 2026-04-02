from __future__ import annotations

import csv
import hashlib
from pathlib import Path


def parse_robinhood_csv(path: Path, account_name: str) -> dict[str, object]:
    transactions: list[dict[str, object | None]] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            description = row["Description"]
            transactions.append(
                {
                    "posted_at": row["Date"],
                    "description": description,
                    "amount": float(row["Amount"]),
                    "merchant": description,
                    "category_hint": None,
                    "external_id": None,
                }
            )
    return {
        "account_name": account_name,
        "source_type": "csv",
        "source_ref": str(path),
        "raw_fingerprint": hashlib.sha256(path.read_bytes()).hexdigest(),
        "transactions": transactions,
    }
