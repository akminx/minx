"""Pure data types for Slice 6 memory proposals and detector pipeline results.

`MemoryProposal` is structured intent from detectors (no I/O). `DetectorResult`
bundles insight candidates with optional memory proposals for one detector run
or an aggregated pipeline run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from minx_mcp.core.models import InsightCandidate


@dataclass(frozen=True)
class MemoryProposal:
    """Immutable proposal to create or update a memory (no DB writes here)."""

    memory_type: str
    scope: str
    subject: str
    confidence: float
    payload: Mapping[str, Any]
    source: str
    reason: str

    def __post_init__(self) -> None:
        if isinstance(self.payload, MappingProxyType):
            return
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class DetectorResult:
    """Output of a detector function or the full detector pipeline."""

    insights: tuple[InsightCandidate, ...]
    memory_proposals: tuple[MemoryProposal, ...] = ()

    @classmethod
    def empty(cls) -> DetectorResult:
        return cls((), ())

    @classmethod
    def insights_only(cls, *insights: InsightCandidate) -> DetectorResult:
        return cls(insights=insights, memory_proposals=())
