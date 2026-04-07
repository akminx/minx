from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from minx_mcp.finance.import_models import (
    GenericCSVMapping,
    ParsedImportBatch,
    ParsedTransaction,
)
from minx_mcp.money import parse_dollars_to_cents


def parse_generic_csv(
    path: Path,
    account_name: str,
    mapping: dict[str, object] | GenericCSVMapping,
) -> ParsedImportBatch:
    resolved_mapping = GenericCSVMapping.from_value(mapping)
    transactions: list[ParsedTransaction] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            posted_at = datetime.strptime(
                str(row[resolved_mapping.date_column]),
                resolved_mapping.date_format,
            ).strftime("%Y-%m-%d")
            description = str(row[resolved_mapping.description_column])
            merchant = (
                row.get(resolved_mapping.merchant_column, description)
                if resolved_mapping.merchant_column
                else description
            )
            category_hint = (
                row.get(resolved_mapping.category_hint_column)
                if resolved_mapping.category_hint_column
                else None
            )
            amount_cents = parse_dollars_to_cents(str(row[resolved_mapping.amount_column]))
            transactions.append(
                ParsedTransaction(
                    posted_at=posted_at,
                    description=description,
                    amount_cents=amount_cents,
                    merchant=str(merchant) if merchant is not None else None,
                    category_hint=(
                        str(category_hint) if category_hint is not None else None
                    ),
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
