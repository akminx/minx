from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path


def parse_generic_csv(
    path: Path,
    account_name: str,
    mapping: dict[str, object],
) -> dict[str, object]:
    transactions: list[dict[str, object | None]] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            posted_at = datetime.strptime(
                str(row[str(mapping["date_column"])]),
                str(mapping["date_format"]),
            ).strftime("%Y-%m-%d")
            description = str(row[str(mapping["description_column"])])
            merchant_column = str(mapping.get("merchant_column", ""))
            merchant = row.get(merchant_column, description) if merchant_column else description
            category_column = str(mapping.get("category_hint_column", ""))
            category_hint = row.get(category_column) if category_column else None
            amount = float(row[str(mapping["amount_column"])])
            transactions.append(
                {
                    "posted_at": posted_at,
                    "description": description,
                    "amount": -abs(amount),
                    "merchant": merchant,
                    "category_hint": category_hint,
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
