from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from minx_mcp.contracts import InvalidInputError

_AMOUNT_BODY = re.compile(r"-?(?:\d+\.?\d*|\d*\.\d+)")

_US_GROUPED_AMOUNT = re.compile(
    r"""
    ^
    -?
    (?:
        \d{1,3}(?:,\d{3})+    # "1,234" or "1,234,567"
        (?:\.\d+)?            # optional fractional part
      |
        \d+(?:\.\d+)?         # ungrouped integer/decimal
      |
        \.\d+                 # leading-dot fractional
    )
    $
    """,
    re.VERBOSE,
)


def parse_dollars_to_cents(value: str) -> int:
    raw = value.strip()
    if raw.startswith("USD "):
        raw = raw.removeprefix("USD ").strip()
    elif raw.startswith("$"):
        raw = raw.removeprefix("$").strip()
    if "," in raw and not _US_GROUPED_AMOUNT.fullmatch(raw):
        raise InvalidInputError(
            "amount uses malformed thousands grouping (expected e.g. '1,234.56')"
        )
    normalized = raw.replace(",", "")
    if not _AMOUNT_BODY.fullmatch(normalized):
        raise InvalidInputError("amount contains unsupported characters")
    try:
        amount = Decimal(normalized)
    except (AttributeError, InvalidOperation) as exc:
        raise InvalidInputError("amount must be a valid decimal string") from exc
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise InvalidInputError("amount must use at most 2 decimal places")
    return int((amount * 100).to_integral_exact())


def cents_to_display_dollars(value: int) -> float:
    return float(Decimal(value) / 100)


def format_cents(value: int) -> str:
    sign = "-" if value < 0 else ""
    dollars = Decimal(abs(value)) / 100
    return f"{sign}${dollars:.2f}"


def format_decimal_cents(value: int) -> str:
    """Return a signed two-decimal amount string without a currency symbol (e.g. '-12.34')."""
    sign = "-" if value < 0 else ""
    dollars = (Decimal(abs(value)) / 100).quantize(Decimal("0.01"))
    return f"{sign}{dollars}"
