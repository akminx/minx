"""Shared frontmatter parsing for ``type: minx-memory`` vault notes.

Both :mod:`minx_mcp.core.vault_scanner` and :mod:`minx_mcp.core.vault_reconciler`
must interpret ``minx-memory`` frontmatter identically. Historically each module
carried its own copy, which quietly drifted (e.g. one returned ``None`` where the
other returned ``""`` for optional scope). This module is the single source of
truth for the contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from minx_mcp.contracts import InvalidInputError

RESERVED_MEMORY_KEYS = frozenset(
    {
        "type",
        "domain",
        "scope",
        "memory_key",
        "memory_type",
        "subject",
        "memory_id",
        "updated",
        "tags",
        "title",
        "id",
        "created",
        "payload_json",
        "value_json",
        "sync_base_updated_at",
    }
)


@dataclass(frozen=True)
class MemoryIdentity:
    """Parsed identity of a ``type: minx-memory`` note.

    ``memory_id`` and ``sync_base_updated_at`` are reconciler concerns — the
    scanner only reads ``scope``, ``memory_type``, and ``subject``. The fields
    are always populated here so both callers share one parser.
    """

    scope: str
    memory_type: str
    subject: str
    memory_key: str
    memory_id: int | None
    sync_base_updated_at: str | None


def optional_str(value: Any) -> str | None:
    """Return ``value`` as a string, or ``None`` for missing/``None`` input."""
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def required_str(frontmatter: dict[str, object], key: str) -> str:
    value = optional_str(frontmatter.get(key))
    if value is None or not value.strip():
        raise InvalidInputError(f"{key} is required")
    return value.strip()


def parse_note_scope(
    frontmatter: dict[str, object],
    *,
    strict_alias_match: bool = False,
    required: bool = False,
) -> str | None:
    """Return the scope of a vault note.

    ``scope`` is canonical; ``domain`` is a legacy alias. With
    ``strict_alias_match`` both must agree when both are present. When
    ``required`` is true and neither is set, an ``InvalidInputError`` is raised.
    """
    scope = optional_str(frontmatter.get("scope"))
    domain = optional_str(frontmatter.get("domain"))
    scope_text = scope.strip() if scope is not None else ""
    domain_text = domain.strip() if domain is not None else ""

    if scope_text:
        if strict_alias_match and domain_text and domain_text != scope_text:
            raise InvalidInputError("scope and domain must match")
        return scope_text
    if domain_text:
        return domain_text
    if required:
        raise InvalidInputError("scope is required")
    return None


def parse_memory_identity(frontmatter: dict[str, object]) -> MemoryIdentity:
    """Parse the canonical identity fields of a ``minx-memory`` note."""
    scope = parse_note_scope(frontmatter, strict_alias_match=True, required=True)
    # ``required=True`` guarantees a non-empty string.
    if scope is None:
        raise RuntimeError("internal: parse_note_scope returned None with required=True")
    memory_type = required_str(frontmatter, "memory_type")
    memory_key = required_str(frontmatter, "memory_key")
    parts = memory_key.split(".", 2)
    if len(parts) != 3:
        raise InvalidInputError("memory_key must have format {scope}.{memory_type}.{subject}")
    key_scope, key_memory_type, key_subject = (p.strip() for p in parts)
    if key_scope != scope:
        raise InvalidInputError("memory_key scope does not match note scope")
    if key_memory_type != memory_type:
        raise InvalidInputError("memory_key memory_type does not match memory_type")
    if not key_subject:
        raise InvalidInputError("subject is required")
    subject_field = optional_str(frontmatter.get("subject"))
    if subject_field is not None and subject_field.strip() != key_subject:
        raise InvalidInputError("subject does not match memory_key")

    return MemoryIdentity(
        scope=scope,
        memory_type=memory_type,
        subject=key_subject,
        memory_key=f"{scope}.{memory_type}.{key_subject}",
        memory_id=parse_optional_int(frontmatter.get("memory_id"), "memory_id"),
        sync_base_updated_at=optional_str(frontmatter.get("sync_base_updated_at")),
    )


def parse_memory_payload(
    frontmatter: dict[str, object],
    *,
    allow_implicit: bool,
) -> dict[str, object]:
    """Parse a memory note payload.

    When ``payload_json`` (or the legacy ``value_json`` alias) is present it is
    authoritative. Otherwise, if ``allow_implicit`` is true, the payload is
    inferred by stripping :data:`RESERVED_MEMORY_KEYS` from the frontmatter —
    this supports hand-written notes that express the payload as flat YAML.
    """
    raw_payload = frontmatter.get("payload_json", frontmatter.get("value_json"))
    if raw_payload is not None:
        if isinstance(raw_payload, dict):
            return dict(raw_payload)
        if isinstance(raw_payload, str):
            try:
                parsed = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                raise InvalidInputError("payload_json must be valid JSON") from exc
            if not isinstance(parsed, dict):
                raise InvalidInputError("payload_json must be a JSON object")
            return dict(parsed)
        raise InvalidInputError("payload_json must be a JSON object or JSON string")
    if not allow_implicit:
        raise InvalidInputError("payload_json is required for generated memory notes")
    return {str(k): v for k, v in frontmatter.items() if k not in RESERVED_MEMORY_KEYS}


def parse_optional_int(value: object, field_name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise InvalidInputError(f"{field_name} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise InvalidInputError(f"{field_name} must be an integer")
    if parsed < 1:
        raise InvalidInputError(f"{field_name} must be positive")
    return parsed


__all__ = [
    "RESERVED_MEMORY_KEYS",
    "MemoryIdentity",
    "optional_str",
    "parse_memory_identity",
    "parse_memory_payload",
    "parse_note_scope",
    "parse_optional_int",
    "required_str",
]
