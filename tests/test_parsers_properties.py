"""Hypothesis property tests for money, vault scalar parsers, and frontmatter helpers."""

from __future__ import annotations

import string
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from hypothesis.strategies import composite

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.vault_memory_frontmatter import parse_note_scope, parse_optional_int
from minx_mcp.money import format_cents, format_decimal_cents, parse_dollars_to_cents
from minx_mcp.vault_reader import _parse_scalar, _parse_single_quoted

_FP = Path("x")
_LINENO = 1

_CS: tuple[Literal["Cs"]] = ("Cs",)
_LU_LL: tuple[Literal["Lu"], Literal["Ll"]] = ("Lu", "Ll")
_LU_LL_ND: tuple[Literal["Lu"], Literal["Ll"], Literal["Nd"]] = ("Lu", "Ll", "Nd")
_ND: tuple[Literal["Nd"]] = ("Nd",)

_NO_SURROGATES = st.characters(blacklist_categories=_CS)

_ALNUM_SCOPE = st.text(
    alphabet=st.characters(whitelist_categories=_LU_LL_ND),
    min_size=1,
    max_size=64,
)


@composite
def bool_null_scalar_strings(draw) -> tuple[str, str]:
    word = draw(st.sampled_from(["true", "false", "null"]))
    s = "".join(draw(st.sampled_from([ch.lower(), ch.upper()])) for ch in word)
    return word, s


@composite
def float_decimal_strings(draw) -> str:
    """Strings matching ``_FLOAT_RE`` (at least one digit on each side of '.')."""
    sign = draw(st.sampled_from(["", "-"]))
    left = draw(st.integers(min_value=0, max_value=10**9))
    frac_digits = draw(st.integers(min_value=1, max_value=6))
    frac = draw(st.integers(min_value=0, max_value=10**frac_digits - 1))
    frac_s = str(frac).zfill(frac_digits)
    return f"{sign}{left}.{frac_s}"


_PROP_SETTINGS = settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])


class TestMoneyProperties:
    @_PROP_SETTINGS
    @given(n=st.integers(min_value=-(10**12), max_value=10**12))
    def test_round_trip_integer_cents_via_format_decimal_cents(self, n: int) -> None:
        assert parse_dollars_to_cents(format_decimal_cents(n)) == n

    # format_cents(n) puts the sign before the '$' (e.g. n=-1 -> '-$0.01'). parse_dollars_to_cents
    # only strips '$' when the string starts with '$', so negative amounts are rejected — real defect.
    @_PROP_SETTINGS
    @given(n=st.integers(min_value=0, max_value=10**12))
    def test_round_trip_integer_cents_via_format_cents_non_negative(self, n: int) -> None:
        assert parse_dollars_to_cents(format_cents(n)) == n

    @_PROP_SETTINGS
    @given(n=st.integers(min_value=-(10**12), max_value=-1))
    @pytest.mark.xfail(
        reason=(
            "format_cents emits '-$…' for n<0 but parse_dollars_to_cents does not normalize '-$' "
            "(shrinker example: n=-1 -> '-$0.01')."
        ),
        strict=True,
    )
    def test_round_trip_integer_cents_via_format_cents_negative(self, n: int) -> None:
        assert parse_dollars_to_cents(format_cents(n)) == n

    @_PROP_SETTINGS
    @given(n=st.integers(min_value=-(10**12), max_value=10**12))
    def test_whitespace_around_accepted_amount_is_idempotent(self, n: int) -> None:
        s = format_decimal_cents(n)
        assert parse_dollars_to_cents(f"  {s}  ") == parse_dollars_to_cents(s)

    @_PROP_SETTINGS
    @given(n=st.integers(min_value=0, max_value=10**12))
    def test_leading_minus_negates_parse(self, n: int) -> None:
        abs_str = format_decimal_cents(n)
        assert parse_dollars_to_cents("-" + abs_str) == -parse_dollars_to_cents(abs_str)

    @_PROP_SETTINGS
    @given(
        n=st.integers(min_value=-(10**12), max_value=10**12),
        bad=st.sampled_from(list("abcdefghijklmnopqrstuvwxyzABCDEFGHI:!@#")),
    )
    def test_non_monetary_characters_rejected(self, n: int, bad: str) -> None:
        base = format_decimal_cents(n)
        poisoned = base + bad
        with pytest.raises(InvalidInputError):
            parse_dollars_to_cents(poisoned)


class TestVaultScalarProperties:
    @_PROP_SETTINGS
    @given(n=st.integers(min_value=-(10**9), max_value=10**9))
    def test_integer_round_trip(self, n: int) -> None:
        out = _parse_scalar(str(n), _FP, _LINENO)
        assert isinstance(out, int)
        assert out == n

    @_PROP_SETTINGS
    @given(s=float_decimal_strings())
    def test_float_decimal_string_round_trip(self, s: str) -> None:
        expected = float(s)
        out = _parse_scalar(s, _FP, _LINENO)
        assert isinstance(out, float)
        assert out == expected

    @_PROP_SETTINGS
    @given(pair=bool_null_scalar_strings())
    def test_bool_and_null_case_insensitive(self, pair: tuple[str, str]) -> None:
        word, casing = pair
        out = _parse_scalar(casing, _FP, _LINENO)
        if word == "true":
            assert out is True
        elif word == "false":
            assert out is False
        else:
            assert out is None

    @_PROP_SETTINGS
    @given(
        s=st.text(
            alphabet=st.characters(whitelist_categories=_LU_LL),
            min_size=1,
            max_size=64,
        )
    )
    def test_plain_alpha_string_returns_itself(self, s: str) -> None:
        assume(s.lower() not in ("true", "false", "null"))
        assert _parse_scalar(s, _FP, _LINENO) == s


class TestSingleQuotedProperties:
    @_PROP_SETTINGS
    @given(s=st.text(alphabet=_NO_SURROGATES, max_size=64))
    def test_round_trip_escape_doubling(self, s: str) -> None:
        escaped = "'" + s.replace("'", "''") + "'"
        assert _parse_single_quoted(escaped, _FP, _LINENO) == s


class TestMemoryFrontmatterProperties:
    @_PROP_SETTINGS
    @given(n=st.integers(min_value=1, max_value=10**12))
    def test_parse_optional_int_accepts_int_and_decimal_string(self, n: int) -> None:
        assert parse_optional_int(n, "k") == n
        assert parse_optional_int(str(n), "k") == n

    def test_parse_optional_int_none_returns_none(self) -> None:
        assert parse_optional_int(None, "k") is None

    @_PROP_SETTINGS
    @given(
        prefix=st.text(alphabet=st.characters(whitelist_categories=_ND), max_size=16),
        suffix=st.text(alphabet=st.characters(whitelist_categories=_ND), max_size=16),
        bad=st.sampled_from(list(string.ascii_letters + "_@")),
    )
    def test_parse_optional_int_rejects_non_integer_strings(self, prefix: str, suffix: str, bad: str) -> None:
        assume(bad)  # non-empty bad segment
        s = f"{prefix}{bad}{suffix}"
        assume(not s.isdigit())
        with pytest.raises(InvalidInputError):
            parse_optional_int(s, "k")

    @_PROP_SETTINGS
    @given(scope=_ALNUM_SCOPE)
    def test_parse_note_scope_strict_alias_same(self, scope: str) -> None:
        fm: dict[str, object] = {"scope": scope, "domain": scope}
        assert parse_note_scope(fm, strict_alias_match=True) == scope

    @_PROP_SETTINGS
    @given(s1=_ALNUM_SCOPE, s2=_ALNUM_SCOPE)
    def test_parse_note_scope_strict_alias_mismatch_raises(self, s1: str, s2: str) -> None:
        assume(s1 != s2)
        fm: dict[str, object] = {"scope": s1, "domain": s2}
        with pytest.raises(InvalidInputError, match="scope and domain must match"):
            parse_note_scope(fm, strict_alias_match=True)
