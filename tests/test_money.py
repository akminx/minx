import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.money import (
    cents_to_display_dollars,
    format_cents,
    format_decimal_cents,
    parse_dollars_to_cents,
)


def test_parse_dollars_to_cents_accepts_exact_two_decimal_inputs():
    assert parse_dollars_to_cents("12.34") == 1234
    assert parse_dollars_to_cents("-42.16") == -4216
    assert parse_dollars_to_cents("0") == 0


def test_parse_dollars_to_cents_rejects_more_than_two_decimal_places():
    with pytest.raises(InvalidInputError, match="at most 2 decimal places"):
        parse_dollars_to_cents("12.345")


def test_cents_to_display_dollars_returns_display_floats():
    assert cents_to_display_dollars(1234) == 12.34
    assert cents_to_display_dollars(-4216) == -42.16


def test_format_decimal_cents_two_places() -> None:
    assert format_decimal_cents(1234) == "12.34"
    assert format_decimal_cents(-4216) == "-42.16"


def test_format_cents_returns_currency_string():
    assert format_cents(1234) == "$12.34"
    assert format_cents(-4216) == "-$42.16"


def test_parse_dollars_to_cents_rejects_whitespace_only_input():
    with pytest.raises(InvalidInputError):
        parse_dollars_to_cents("   ")


def test_parse_dollars_to_cents_rejects_empty_string():
    with pytest.raises(InvalidInputError):
        parse_dollars_to_cents("")


def test_parse_dollars_to_cents_handles_very_large_values():
    assert parse_dollars_to_cents("999999999.99") == 99999999999


def test_parse_dollars_to_cents_handles_negative_amounts():
    assert parse_dollars_to_cents("-0.01") == -1
    assert parse_dollars_to_cents("-1000.00") == -100000
