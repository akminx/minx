from __future__ import annotations

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.core.memory_secret_scanning import (
    raise_secret_detected,
    redaction_event_payload,
    scan_memory_input,
)
from minx_mcp.core.secret_scanner import SecretVerdictKind


def _fake_github_token() -> str:
    return "".join(("gh", "p_", "a" * 36))


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
