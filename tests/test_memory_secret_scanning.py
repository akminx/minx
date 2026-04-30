from __future__ import annotations

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.memory_secret_scanning import (
    prepare_validated_memory_write,
    raise_secret_detected,
    redaction_event_payload,
    scan_memory_input,
    scan_payload_only,
)
from minx_mcp.core.secret_scanner import SecretVerdictKind


def _fake_github_token() -> str:
    return "".join(("gh", "p_", "a" * 36))


def _fake_github_token_with(char: str) -> str:
    return "".join(("gh", "p_", char * 36))


def test_public_memory_secret_scanner_redacts_payload_values_without_raw_secret() -> None:
    secret = _fake_github_token()

    result = scan_memory_input(
        memory_type="preference",
        scope="core",
        subject="api",
        payload={"value": secret},
        source="user",
        reason="manual",
    )

    assert result.verdict is SecretVerdictKind.REDACTED
    assert result.payload == {"value": "[REDACTED:github_token]"}
    event_payload = redaction_event_payload(result)
    assert event_payload == {
        "secret_redacted": {
            "detected_kinds": ["github_token"],
            "fields": ["payload.value"],
        }
    }
    assert secret not in str(result)
    assert secret not in str(event_payload)


def test_payload_secret_key_redaction_preserves_colliding_entries() -> None:
    first_key = _fake_github_token_with("a")
    second_key = _fake_github_token_with("b")

    result = scan_payload_only({first_key: "first", second_key: "second"})

    assert result.verdict is SecretVerdictKind.BLOCK
    assert result.payload == {
        "[REDACTED_KEY]": "first",
        "[REDACTED_KEY_2]": "second",
    }
    assert [loc.field for loc in result.error_locations] == [
        "payload.[REDACTED_KEY]",
        "payload.[REDACTED_KEY_2]",
    ]
    assert first_key not in str(result)
    assert second_key not in str(result)


def test_payload_secret_key_redaction_preserves_literal_placeholder_keys() -> None:
    secret_key = _fake_github_token_with("c")

    result = scan_payload_only({secret_key: "secret-keyed", "[REDACTED_KEY]": "literal"})

    assert result.verdict is SecretVerdictKind.BLOCK
    assert result.payload == {
        "[REDACTED_KEY]": "secret-keyed",
        "[REDACTED_KEY_2]": "literal",
    }
    assert [loc.field for loc in result.error_locations] == ["payload.[REDACTED_KEY]"]
    assert secret_key not in str(result)


def test_public_memory_secret_scanner_blocks_identity_fields_without_raw_secret() -> None:
    secret = _fake_github_token()
    result = scan_memory_input(
        memory_type="preference",
        scope="core",
        subject=secret,
        payload={"value": "safe"},
        source="user",
        reason="manual",
    )

    assert result.verdict is SecretVerdictKind.BLOCK
    with pytest.raises(InvalidInputError) as excinfo:
        raise_secret_detected(result)

    assert excinfo.value.data["kind"] == "secret_detected"
    assert excinfo.value.data["surface"] == "memory"
    assert secret not in str(excinfo.value.data)


def test_prepare_validated_memory_write_scans_validates_and_rescans() -> None:
    secret = _fake_github_token()

    prepared = prepare_validated_memory_write(
        memory_type="preference",
        scope="core",
        subject="api",
        payload={"value": secret},
        source="user",
        reason="manual",
    )

    assert prepared.memory_type == "preference"
    assert prepared.scope == "core"
    assert prepared.subject == "api"
    assert prepared.payload == {"value": "[REDACTED:github_token]"}
    assert prepared.redaction_payload == {
        "secret_redacted": {
            "detected_kinds": ["github_token"],
            "fields": ["payload.value"],
        }
    }
    assert secret not in str(prepared)


def test_prepare_validated_memory_write_blocks_secret_in_identity_fields() -> None:
    secret = _fake_github_token()

    with pytest.raises(InvalidInputError) as excinfo:
        prepare_validated_memory_write(
            memory_type="preference",
            scope="core",
            subject=secret,
            payload={"value": "safe"},
            source="user",
            reason="manual",
        )

    assert excinfo.value.data["kind"] == "secret_detected"
    assert secret not in str(excinfo.value.data)
