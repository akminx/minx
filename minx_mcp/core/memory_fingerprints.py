"""Canonical memory content fingerprint helpers shared by memory write paths."""

from __future__ import annotations

import json

from minx_mcp.core.fingerprint import content_fingerprint, normalize_for_fingerprint
from minx_mcp.core.memory_payloads import PAYLOAD_MODELS

_FINGERPRINTED_MEMORY_TYPES = frozenset({"preference", "pattern", "entity_fact", "constraint"})


def fingerprinted_memory_types() -> frozenset[str]:
    """Return registered payload types with explicit content-fingerprint mappings."""

    return _FINGERPRINTED_MEMORY_TYPES


def _canonical_aliases(aliases: object) -> str:
    """Canonical JSON form of an aliases list for fingerprinting."""

    if not aliases:
        return ""
    if not isinstance(aliases, list | tuple):
        return ""
    normalized = sorted(normalize_for_fingerprint(str(a)) for a in aliases)
    return json.dumps(normalized, ensure_ascii=False)


def memory_fingerprint_input(
    memory_type: str,
    payload: dict[str, object],
    *,
    scope: str,
    subject: str,
) -> tuple[str, str, str, str, str]:
    """Return the 5-tuple (memory_type, scope, subject, note, value_part)."""

    note = str(payload.get("note") or "")

    if memory_type == "preference":
        value_part = str(payload.get("value") or "")
    elif memory_type == "pattern":
        value_part = str(payload.get("signal") or "")
    elif memory_type == "entity_fact":
        value_part = _canonical_aliases(payload.get("aliases"))
    elif memory_type == "constraint":
        value_part = str(payload.get("limit_value") or "")
    elif memory_type in PAYLOAD_MODELS:
        raise RuntimeError(
            f"memory_fingerprint_input missing per-type mapping for "
            f"registered memory_type={memory_type!r}; update the function "
            "to add it (see Slice 6g spec §5.2)"
        )
    else:
        note = ""
        value_part = json.dumps(payload, sort_keys=True, ensure_ascii=False)

    return (memory_type, scope, subject, note, value_part)


def memory_content_fingerprint(
    memory_type: str,
    payload: dict[str, object],
    *,
    scope: str,
    subject: str,
) -> str:
    """Compute the canonical fingerprint for a memory row's logical content."""

    return content_fingerprint(
        *memory_fingerprint_input(
            memory_type,
            payload,
            scope=scope,
            subject=subject,
        )
    )
