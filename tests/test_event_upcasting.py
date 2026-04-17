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


def test_upcast_payload_skips_upcasters_at_or_below_schema_version():
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
        # schema_version=2 means the stored payload is already at v2, so only
        # upcasters for versions strictly greater than 2 should be applied
        # (condition: schema_version < version).
        result = _upcast_payload("test.versioned2", {"base": True}, schema_version=2)
        assert result["base"] is True
        assert "added_in_v1" not in result
        assert "added_in_v2" not in result
        assert result["added_in_v3"] is True
    finally:
        for key in upcasters:
            PAYLOAD_UPCASTERS.pop(key, None)
        PAYLOAD_UPCASTERS.update(original_upcasters)


def test_upcast_payload_skips_all_upcasters_when_at_latest_schema_version():
    upcasters = {
        "test.versioned3": {
            1: lambda p: {**p, "added_in_v1": True},
            2: lambda p: {**p, "added_in_v2": True},
        }
    }
    original_upcasters = dict(PAYLOAD_UPCASTERS)
    PAYLOAD_UPCASTERS.update(upcasters)
    try:
        # schema_version=2 equals the latest registered version; no upcaster
        # should run because the payload is already in its target shape.
        result = _upcast_payload("test.versioned3", {"base": True}, schema_version=2)
        assert result == {"base": True}
    finally:
        for key in upcasters:
            PAYLOAD_UPCASTERS.pop(key, None)
        PAYLOAD_UPCASTERS.update(original_upcasters)


def test_upcast_payload_does_not_reapply_upcaster_at_stored_version():
    """Regression test: upcasters must not be re-applied to payloads already at their version.

    Non-idempotent upcasters (e.g. those that increment a counter or append to
    a list) would corrupt data if the target version ran on a payload that was
    already stored at that version.
    """
    calls: list[str] = []

    def v1_upcaster(p: dict[str, object]) -> dict[str, object]:
        calls.append("v1")
        return {**p, "v1_applications": int(p.get("v1_applications", 0) or 0) + 1}

    upcasters = {"test.nonidempotent": {1: v1_upcaster}}
    original_upcasters = dict(PAYLOAD_UPCASTERS)
    PAYLOAD_UPCASTERS.update(upcasters)
    try:
        payload = {"v1_applications": 1}
        result = _upcast_payload("test.nonidempotent", payload, schema_version=1)
        assert calls == []
        assert result == {"v1_applications": 1}
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
