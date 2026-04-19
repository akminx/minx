"""Shared frontmatter conventions for ``type: minx-memory`` vault notes."""

from __future__ import annotations

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
