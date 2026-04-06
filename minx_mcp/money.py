from __future__ import annotations

from decimal import Decimal, InvalidOperation

from minx_mcp.contracts import InvalidInputError


def parse_dollars_to_cents(value: str) -> int:
    try:
        amount = Decimal(value.strip())
    except (AttributeError, InvalidOperation) as exc:
        raise InvalidInputError("amount must be a valid decimal string") from exc
    exponent = amount.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise InvalidInputError("amount must use at most 2 decimal places")
    return int((amount * 100).to_integral_exact())


def cents_to_dollars(value: int) -> float:
    return float(Decimal(value) / 100)


def format_cents(value: int) -> str:
    sign = "-" if value < 0 else ""
    dollars = Decimal(abs(value)) / 100
    return f"{sign}${dollars:.2f}"
