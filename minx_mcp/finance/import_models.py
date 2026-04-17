from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from minx_mcp.contracts import InvalidInputError


@dataclass(frozen=True)
class ParsedTransaction:
    posted_at: str
    description: str
    amount_cents: int
    merchant: str | None
    category_hint: str | None
    external_id: str | None


@dataclass(frozen=True)
class ParsedImportBatch:
    account_name: str
    source_type: str
    source_ref: str
    raw_fingerprint: str
    transactions: list[ParsedTransaction]

    def with_source_metadata(self, source_ref: str, raw_fingerprint: str) -> ParsedImportBatch:
        return replace(self, source_ref=source_ref, raw_fingerprint=raw_fingerprint)


@dataclass(frozen=True)
class GenericCSVMapping:
    date_column: str
    amount_column: str
    description_column: str
    date_format: str
    merchant_column: str | None = None
    category_hint_column: str | None = None

    @classmethod
    def from_value(
        cls,
        value: object,
    ) -> GenericCSVMapping:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise InvalidInputError("generic csv mapping must be a mapping object")

        mapping: dict[str, Any] = value

        required_fields = (
            "date_column",
            "amount_column",
            "description_column",
            "date_format",
        )
        missing = [
            field
            for field in required_fields
            if not isinstance(mapping.get(field), str) or not str(mapping[field]).strip()
        ]
        if missing:
            raise InvalidInputError(f"generic csv mapping is missing required field: {missing[0]}")

        merchant_column = mapping.get("merchant_column")
        category_hint_column = mapping.get("category_hint_column")

        return cls(
            date_column=str(mapping["date_column"]),
            amount_column=str(mapping["amount_column"]),
            description_column=str(mapping["description_column"]),
            date_format=str(mapping["date_format"]),
            merchant_column=(
                str(merchant_column)
                if isinstance(merchant_column, str) and merchant_column
                else None
            ),
            category_hint_column=(
                str(category_hint_column)
                if isinstance(category_hint_column, str) and category_hint_column
                else None
            ),
        )
