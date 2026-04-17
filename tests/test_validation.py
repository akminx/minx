from __future__ import annotations

from datetime import date

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.validation import (
    reject_unknown_keys,
    require_bool,
    require_exact_keys,
    require_int,
    require_non_empty,
    require_optional_str,
    require_payload_object,
    require_str,
    require_str_list,
    resolve_date_or_today,
    validate_date_window,
    validate_iso_date,
    validate_optional_date_range,
)


class TestValidateIsoDate:
    def test_accepts_valid_iso_date(self):
        assert validate_iso_date("2026-04-17", field_name="when") == date(2026, 4, 17)

    def test_rejects_invalid_date_with_contract_error(self):
        with pytest.raises(InvalidInputError, match="when must be a valid ISO date"):
            validate_iso_date("not-a-date", field_name="when")


class TestValidateDateWindow:
    def test_returns_dates_in_order(self):
        start, end = validate_date_window("2026-04-01", "2026-04-15")
        assert start == date(2026, 4, 1)
        assert end == date(2026, 4, 15)

    def test_rejects_inverted_window(self):
        with pytest.raises(InvalidInputError, match="period_start must be on or before period_end"):
            validate_date_window("2026-04-15", "2026-04-01")

    def test_rejects_invalid_component(self):
        with pytest.raises(InvalidInputError):
            validate_date_window("2026-04-01", "bad")


class TestValidateOptionalDateRange:
    def test_handles_none_pair(self):
        assert validate_optional_date_range(None, None) == (None, None)

    def test_handles_only_start(self):
        start, end = validate_optional_date_range("2026-04-01", None)
        assert start == date(2026, 4, 1)
        assert end is None

    def test_rejects_inverted_range(self):
        with pytest.raises(InvalidInputError):
            validate_optional_date_range("2026-04-15", "2026-04-01")


class TestRequireNonEmpty:
    def test_returns_value_for_non_empty(self):
        assert require_non_empty("field", "value") == "value"

    def test_rejects_whitespace_only(self):
        with pytest.raises(InvalidInputError, match="field must not be empty"):
            require_non_empty("field", "   ")


class TestResolveDateOrToday:
    def test_returns_provided_date_when_valid(self):
        assert resolve_date_or_today("2026-04-17", field_name="review_date") == "2026-04-17"

    def test_falls_back_to_today_for_none(self):
        result = resolve_date_or_today(None, field_name="review_date")
        assert result == date.today().isoformat()

    def test_rejects_invalid_iso_date(self):
        with pytest.raises(InvalidInputError):
            resolve_date_or_today("nope", field_name="review_date")


class TestRequirePayloadObject:
    def test_accepts_dict(self):
        payload = {"key": "value"}
        assert require_payload_object(payload, field_name="payload") is payload

    def test_rejects_non_dict(self):
        with pytest.raises(InvalidInputError, match="payload must be an object"):
            require_payload_object(["not", "a", "dict"], field_name="payload")


class TestRequireStr:
    def test_returns_string_value(self):
        assert require_str({"name": "goal"}, "name") == "goal"

    def test_rejects_non_string(self):
        with pytest.raises(InvalidInputError, match="name must be a string"):
            require_str({"name": 123}, "name")

    def test_rejects_missing(self):
        with pytest.raises(InvalidInputError):
            require_str({}, "name")


class TestRequireOptionalStr:
    def test_returns_none_when_missing_or_none(self):
        assert require_optional_str({}, "notes") is None
        assert require_optional_str({"notes": None}, "notes") is None

    def test_returns_string_value(self):
        assert require_optional_str({"notes": "hello"}, "notes") == "hello"

    def test_rejects_non_string(self):
        with pytest.raises(InvalidInputError):
            require_optional_str({"notes": 1}, "notes")


class TestRequireInt:
    def test_accepts_int(self):
        assert require_int({"count": 5}, "count") == 5

    def test_rejects_bool(self):
        with pytest.raises(InvalidInputError):
            require_int({"count": True}, "count")

    def test_rejects_non_int(self):
        with pytest.raises(InvalidInputError):
            require_int({"count": "5"}, "count")


class TestRequireBool:
    def test_returns_default_when_missing(self):
        assert require_bool({}, "flag", default=True) is True

    def test_returns_explicit_value(self):
        assert require_bool({"flag": False}, "flag", default=True) is False

    def test_rejects_non_bool_values(self):
        with pytest.raises(InvalidInputError):
            require_bool({"flag": "yes"}, "flag", default=True)


class TestRequireStrList:
    def test_accepts_list_of_strings(self):
        assert require_str_list({"items": ["a", "b"]}, "items") == ["a", "b"]

    def test_rejects_mixed_list(self):
        with pytest.raises(InvalidInputError):
            require_str_list({"items": ["a", 1]}, "items")

    def test_rejects_non_list(self):
        with pytest.raises(InvalidInputError):
            require_str_list({"items": "a"}, "items")


class TestRequireExactKeys:
    def test_accepts_exact_keys(self):
        require_exact_keys({"a": 1, "b": 2}, {"a", "b"}, context="test")

    def test_rejects_missing_key(self):
        with pytest.raises(InvalidInputError, match="missing required fields: b"):
            require_exact_keys({"a": 1}, {"a", "b"}, context="test")

    def test_rejects_unknown_key(self):
        with pytest.raises(InvalidInputError, match="unknown fields: extra"):
            require_exact_keys({"a": 1, "b": 2, "extra": 3}, {"a", "b"}, context="test")


class TestRejectUnknownKeys:
    def test_allows_subset(self):
        reject_unknown_keys({"a": 1}, {"a", "b"}, context="test")

    def test_rejects_unknown(self):
        with pytest.raises(InvalidInputError, match="unknown fields: c"):
            reject_unknown_keys({"a": 1, "c": 2}, {"a", "b"}, context="test")
