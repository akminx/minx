from __future__ import annotations

from minx_mcp.core.memory_fingerprints import fingerprinted_memory_types
from minx_mcp.core.memory_payloads import PAYLOAD_MODELS


def test_registered_payload_models_have_fingerprint_mappings() -> None:
    assert set(PAYLOAD_MODELS) <= fingerprinted_memory_types()
