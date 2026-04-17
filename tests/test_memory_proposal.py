from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from minx_mcp.core.memory_models import MemoryProposal


def test_memory_proposal_required_fields_and_frozen() -> None:
    proposal = MemoryProposal(
        memory_type="pattern",
        scope="finance",
        subject="merchant:normalized",
        confidence=0.82,
        payload={"k": "v", "n": 1},
        source="detector",
        reason="recurring spend",
    )
    assert proposal.memory_type == "pattern"
    assert proposal.scope == "finance"
    assert proposal.subject == "merchant:normalized"
    assert proposal.confidence == 0.82
    assert proposal.source == "detector"
    assert proposal.reason == "recurring spend"
    assert dict(proposal.payload) == {"k": "v", "n": 1}
    assert isinstance(proposal.payload, MappingProxyType)

    with pytest.raises(FrozenInstanceError):
        proposal.memory_type = "other"  # type: ignore[misc]

    with pytest.raises(TypeError):
        proposal.payload["k"] = "mutated"  # type: ignore[index]


def test_memory_proposal_accepts_mapping_proxy_idempotently() -> None:
    inner = {"a": True}
    first = MemoryProposal(
        memory_type="preference",
        scope="meals",
        subject="diet",
        confidence=1.0,
        payload=inner,
        source="user",
        reason="stated",
    )
    second = MemoryProposal(
        memory_type="preference",
        scope="meals",
        subject="diet",
        confidence=1.0,
        payload=first.payload,
        source="user",
        reason="stated",
    )
    assert first.payload is second.payload
