from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from minx_mcp.contracts import InvalidInputError

if TYPE_CHECKING:
    from minx_mcp.core.memory_models import MemoryRecord

CAPTURE_TYPE_MAX_BYTES = 64
SUBJECT_MAX_BYTES = 200
METADATA_MAX_TOP_LEVEL_KEYS = 32
METADATA_MAX_DEPTH = 4
METADATA_STRING_MAX_BYTES = 4096


def normalize_capture_text_for_body(text: str) -> str:
    return _collapse_whitespace(text.strip())


def normalize_capture_type(raw: str) -> str:
    stripped = raw.strip()
    if not stripped:
        return "observation"

    lowered = "".join(char.lower() if "A" <= char <= "Z" else char for char in stripped)
    normalized = re.sub(r"\s+", "_", lowered)
    normalized = re.sub(r"[^a-z0-9_-]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        return "observation"
    return _truncate_utf8_with_ellipsis(normalized, CAPTURE_TYPE_MAX_BYTES)


def derive_capture_subject(
    *,
    capture_type_normalized: str,
    raw_text: str,
    explicit_subject: str | None,
) -> str:
    if explicit_subject is not None:
        subject = explicit_subject.strip()
        if not subject:
            raise InvalidInputError("subject must be non-empty")
        return _truncate_utf8_with_ellipsis(subject, SUBJECT_MAX_BYTES)

    fragment = "capture"
    for line in raw_text.splitlines():
        collapsed = _collapse_whitespace(line.strip())
        if collapsed:
            fragment = collapsed
            break
    return _truncate_utf8_with_ellipsis(
        f"{capture_type_normalized}:{fragment}",
        SUBJECT_MAX_BYTES,
    )


def validate_capture_metadata(meta: object) -> dict[str, object] | None:
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise InvalidInputError("metadata must be a JSON object")
    if not meta:
        return None
    if len(meta) > METADATA_MAX_TOP_LEVEL_KEYS:
        raise InvalidInputError(
            f"metadata must contain at most {METADATA_MAX_TOP_LEVEL_KEYS} top-level keys"
        )
    _validate_metadata_node(meta, depth=1)
    return dict(meta)


def build_captured_thought_payload(
    *,
    text: str,
    capture_type: str,
    metadata: dict[str, object] | None,
) -> dict[str, object]:
    payload: dict[str, object] = {"text": text, "capture_type": capture_type}
    if metadata is not None:
        payload["metadata"] = metadata
    return payload


def build_capture_response_slots(*, record: MemoryRecord, capture_type: str) -> dict[str, object]:
    return {
        "memory_id": record.id,
        "status": record.status,
        "memory_type": record.memory_type,
        "scope": record.scope,
        "subject": record.subject,
        "capture_type": capture_type,
    }


def _truncate_utf8_with_ellipsis(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    suffix = "..."
    suffix_len = len(suffix.encode("utf-8"))
    budget = max_bytes - suffix_len
    if budget <= 0:
        return suffix[:max_bytes]

    out: list[str] = []
    used = 0
    for char in value:
        char_len = len(char.encode("utf-8"))
        if used + char_len > budget:
            break
        out.append(char)
        used += char_len
    return "".join(out) + suffix


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def _validate_metadata_node(value: object, *, depth: int) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidInputError("metadata numeric values must be finite")
        return
    if isinstance(value, str):
        if len(value.encode("utf-8")) > METADATA_STRING_MAX_BYTES:
            raise InvalidInputError(
                f"metadata string values must be at most {METADATA_STRING_MAX_BYTES} bytes"
            )
        return

    if isinstance(value, Mapping):
        if depth > METADATA_MAX_DEPTH:
            raise InvalidInputError(f"metadata nesting depth must be at most {METADATA_MAX_DEPTH}")
        for key, child in value.items():
            if not isinstance(key, str):
                raise InvalidInputError("metadata keys must be strings")
            _validate_metadata_node(child, depth=depth + 1)
        return

    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        if depth > METADATA_MAX_DEPTH:
            raise InvalidInputError(f"metadata nesting depth must be at most {METADATA_MAX_DEPTH}")
        for child in value:
            _validate_metadata_node(child, depth=depth + 1)
        return

    raise InvalidInputError("metadata values must be JSON-compatible")

