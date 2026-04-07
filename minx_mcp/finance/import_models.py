from __future__ import annotations

from dataclasses import dataclass, replace

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

    def with_source_metadata(self, source_ref: str, raw_fingerprint: str) -> "ParsedImportBatch":
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
        value: dict[str, object] | "GenericCSVMapping",
    ) -> "GenericCSVMapping":
        if isinstance(value, cls):
            return value

        required_fields = (
            "date_column",
            "amount_column",
            "description_column",
            "date_format",
        )
        missing = [
            field
            for field in required_fields
            if not isinstance(value.get(field), str) or not str(value[field]).strip()
        ]
        if missing:
            raise InvalidInputError(
                f"generic csv mapping is missing required field: {missing[0]}"
            )

        merchant_column = value.get("merchant_column")
        category_hint_column = value.get("category_hint_column")

        return cls(
            date_column=str(value["date_column"]),
            amount_column=str(value["amount_column"]),
            description_column=str(value["description_column"]),
            date_format=str(value["date_format"]),
            merchant_column=(
                str(merchant_column) if isinstance(merchant_column, str) and merchant_column else None
            ),
            category_hint_column=(
                str(category_hint_column)
                if isinstance(category_hint_column, str) and category_hint_column
                else None
            ),
        )
