from __future__ import annotations

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.memory_payloads import (
    PAYLOAD_MODELS,
    coerce_prior_payload_to_schema,
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


def test_validate_preserves_sparse_shape() -> None:
    """Previously dumped with exclude_none=False, which expanded {} into a
    full dict of None values. The fix in f2 is to use exclude_unset=True so
    the on-disk JSON stays sparse and downstream 'key in payload' presence
    checks keep working."""
    out = validate_memory_payload("preference", {"note": "hello"})
    assert out == {"note": "hello"}
    assert "category" not in out
    assert "value" not in out


def test_validate_empty_payload_stays_empty() -> None:
    out = validate_memory_payload("preference", {})
    assert out == {}


def test_validate_strips_whitespace_in_strings() -> None:
    # pydantic's str_strip_whitespace=True on _ExtraForbidModel
    out = validate_memory_payload("preference", {"note": "  hello  "})
    assert out == {"note": "hello"}


def test_coerce_prior_payload_drops_unknown_keys_for_canonical_type() -> None:
    """Legacy/pre-schema payloads with unknown keys get normalized against
    the model on merge, so junk decays out rather than compounding."""
    prior: dict[str, object] = {"note": "keep", "legacy_key": "drop", "stale": 42}
    out = coerce_prior_payload_to_schema("preference", prior)
    assert out == {"note": "keep"}


def test_coerce_prior_payload_returns_empty_on_unrecoverable_prior() -> None:
    """If every field is wrong type / unknown, return {} rather than raising —
    merge should not fail because the prior row is legacy garbage."""
    prior: dict[str, object] = {"observed_count": "not_an_int", "unknown_field": "x"}
    out = coerce_prior_payload_to_schema("pattern", prior)
    # observed_count is wrong type -> filtered to known keys only -> {observed_count: "not_an_int"}
    # -> validate fails -> return {}
    assert out == {}


def test_coerce_prior_payload_unknown_memory_type_is_noop() -> None:
    prior: dict[str, object] = {"anything": "goes"}
    assert coerce_prior_payload_to_schema("identity", prior) == prior


def test_coerce_prior_payload_preserves_valid_subset() -> None:
    prior: dict[str, object] = {"note": "ok", "frequency": "weekly", "junk": True}
    out = coerce_prior_payload_to_schema("pattern", prior)
    assert out == {"note": "ok", "frequency": "weekly"}
