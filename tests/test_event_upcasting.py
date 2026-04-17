from __future__ import annotations

import pytest

from minx_mcp.core.events import (
    PAYLOAD_UPCASTERS,
    _upcast_payload,
    _validate_upcaster_contiguity,
)


def test_empty_upcaster_registry_is_valid():
    _validate_upcaster_contiguity({})


def test_contiguous_upcaster_registry_is_valid():
    registry = {
        "test.event": {
            1: lambda p: {**p, "v1_field": "added"},
            2: lambda p: {**p, "v2_field": "added"},
        }
    }
    _validate_upcaster_contiguity(registry)  # should not raise


def test_contiguity_check_raises_on_gap():
    registry = {
        "test.event": {
            1: lambda p: {**p, "v1": True},
            3: lambda p: {**p, "v3": True},  # gap: missing version 2
        }
    }
    with pytest.raises(ValueError, match="non-contiguous version keys"):
        _validate_upcaster_contiguity(registry)


def test_contiguity_check_raises_when_starting_above_one():
    registry = {
        "test.event": {
            2: lambda p: {**p, "v2": True},
            3: lambda p: {**p, "v3": True},
        }
    }
    with pytest.raises(ValueError, match="must start at 1"):
        _validate_upcaster_contiguity(registry)


def test_upcast_payload_applies_chain_from_v1_to_latest():
    upcasters = {
        "test.versioned": {
            1: lambda p: {**p, "added_in_v1": True},
            2: lambda p: {**p, "added_in_v2": True},
        }
    }
    original_upcasters = dict(PAYLOAD_UPCASTERS)
    PAYLOAD_UPCASTERS.update(upcasters)
    try:
        result = _upcast_payload("test.versioned", {"original": "value"}, schema_version=0)
        assert result["original"] == "value"
        assert result["added_in_v1"] is True
        assert result["added_in_v2"] is True
    finally:
        # Restore original state
        for key in upcasters:
            PAYLOAD_UPCASTERS.pop(key, None)
        PAYLOAD_UPCASTERS.update(original_upcasters)


def test_upcast_payload_skips_upcasters_for_versions_below_schema_version():
    upcasters = {
        "test.versioned2": {
            1: lambda p: {**p, "added_in_v1": True},
            2: lambda p: {**p, "added_in_v2": True},
            3: lambda p: {**p, "added_in_v3": True},
        }
    }
    original_upcasters = dict(PAYLOAD_UPCASTERS)
    PAYLOAD_UPCASTERS.update(upcasters)
    try:
        # schema_version=3 means stored schema is already at v3; upcasters for
        # versions < schema_version are skipped (condition: schema_version <= version).
        # 3<=1 False, 3<=2 False, 3<=3 True — only v3 runs.
        result = _upcast_payload("test.versioned2", {"base": True}, schema_version=3)
        assert result["base"] is True
        assert "added_in_v1" not in result
        assert "added_in_v2" not in result
        assert result["added_in_v3"] is True
    finally:
        for key in upcasters:
            PAYLOAD_UPCASTERS.pop(key, None)
        PAYLOAD_UPCASTERS.update(original_upcasters)


def test_upcast_payload_returns_unchanged_when_no_upcasters():
    payload = {"key": "value"}
    result = _upcast_payload("unknown.event.type", payload, schema_version=0)
    assert result == payload
    # When no upcasters are registered, the original dict is returned as-is.
    assert result is payload
