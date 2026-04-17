from __future__ import annotations

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.memory_payloads import (
    PAYLOAD_MODELS,
    validate_memory_payload,
)


def test_unknown_memory_type_passes_through() -> None:
    payload: dict[str, object] = {"a": 1, "b": "x"}
    assert validate_memory_payload("identity", payload) is payload


def test_preference_payload_rejects_unknown_field() -> None:
    with pytest.raises(InvalidInputError, match="wrong_key"):
        validate_memory_payload("preference", {"note": "x", "wrong_key": "y"})


def test_pattern_payload_accepts_valid_shape() -> None:
    out = validate_memory_payload(
        "pattern",
        {"note": "n", "frequency": "weekly", "observed_count": 3},
    )
    assert out["frequency"] == "weekly"
    assert out["observed_count"] == 3


def test_pattern_payload_rejects_negative_observed_count() -> None:
    with pytest.raises(InvalidInputError, match="observed_count"):
        validate_memory_payload("pattern", {"observed_count": -1})


def test_entity_fact_payload_accepts_aliases_list() -> None:
    assert validate_memory_payload("entity_fact", {"aliases": ["a", "b"]})["aliases"] == ["a", "b"]
    with pytest.raises(InvalidInputError, match="aliases"):
        validate_memory_payload("entity_fact", {"aliases": "a"})


def test_constraint_payload_validates() -> None:
    out = validate_memory_payload(
        "constraint",
        {"kind": "budget", "limit_value": "200", "unit": "USD/week", "note": "cap"},
    )
    assert out["kind"] == "budget"
    assert out["limit_value"] == "200"
    assert out["unit"] == "USD/week"


def test_validation_error_message_names_field() -> None:
    with pytest.raises(InvalidInputError) as excinfo:
        validate_memory_payload("preference", {"oops": 1})
    assert "oops" in str(excinfo.value)


def test_empty_payload_validates_for_every_type() -> None:
    for memory_type in PAYLOAD_MODELS:
        validate_memory_payload(memory_type, {})
