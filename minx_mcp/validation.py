from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date

from minx_mcp.contracts import InvalidInputError


class InvalidPayloadJSONError(InvalidInputError):
    """Raised when a stored ``payload_json`` blob is missing/corrupt.

    Carries structured context (``label``, ``source_id``) so callers can
    skip-and-log specific rows without substring-matching the message.
    """

    def __init__(self, message: str, *, label: str, source_id: int | None = None) -> None:
        super().__init__(
            message,
            data={"kind": "invalid_payload_json", "label": label, "source_id": source_id},
        )
        self.label = label
        self.source_id = source_id


def parse_payload_json(
    raw: str, *, label: str, source_id: int | None = None
) -> dict[str, object]:
    """Parse a stored JSON object payload, raising ``InvalidPayloadJSONError`` on failure."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidPayloadJSONError(
            f"stored {label} payload_json is not valid JSON",
            label=label,
            source_id=source_id,
        ) from exc
    if not isinstance(parsed, dict):
        raise InvalidPayloadJSONError(
            f"stored {label} payload_json must be a JSON object",
            label=label,
            source_id=source_id,
        )
    return parsed


def validate_iso_date(value: str, *, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInputError(f"{field_name} must be a valid ISO date") from exc


def validate_date_window(
    start: str,
    end: str,
    *,
    start_field: str = "period_start",
    end_field: str = "period_end",
    invalid_date_message: str = "Invalid ISO date",
) -> tuple[date, date]:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise InvalidInputError(invalid_date_message) from exc
    if start_date > end_date:
        raise InvalidInputError(f"{start_field} must be on or before {end_field}")
    return start_date, end_date


def validate_optional_date_range(
    start: str | None,
    end: str | None,
    *,
    start_field: str = "start_date",
    end_field: str = "end_date",
    invalid_date_message: str = "Invalid ISO date",
) -> tuple[date | None, date | None]:
    start_date = None
    end_date = None
    if start is not None:
        try:
            start_date = date.fromisoformat(start)
        except ValueError as exc:
            raise InvalidInputError(invalid_date_message) from exc
    if end is not None:
        try:
            end_date = date.fromisoformat(end)
        except ValueError as exc:
            raise InvalidInputError(invalid_date_message) from exc
    if start_date is not None and end_date is not None and start_date > end_date:
        raise InvalidInputError(f"{start_field} must be on or before {end_field}")
    return start_date, end_date


def require_non_empty(name: str, value: str) -> str:
    if not value.strip():
        raise InvalidInputError(f"{name} must not be empty")
    return value


def resolve_date_or_today(value: str | None, *, field_name: str) -> str:
    effective = value if value is not None else date.today().isoformat()
    validate_iso_date(effective, field_name=field_name)
    return effective


def require_payload_object(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InvalidInputError(f"{field_name} must be an object")
    return value


def require_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise InvalidInputError(f"{key} must be a string")
    return value


def require_optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidInputError(f"{key} must be a string when provided")
    return value


def require_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInputError(f"{key} must be an integer")
    return value


def require_bool(payload: Mapping[str, object], key: str, *, default: bool) -> bool:
    if key not in payload:
        return default
    value = payload[key]
    if not isinstance(value, bool):
        raise InvalidInputError(f"{key} must be a boolean")
    return value


def require_str_list(payload: Mapping[str, object], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise InvalidInputError(f"{key} must be a list of strings")
    return value


def require_exact_keys(
    payload: Mapping[str, object],
    required: set[str],
    *,
    context: str,
) -> None:
    missing_keys = required - set(payload)
    if missing_keys:
        missing_list = ", ".join(sorted(missing_keys))
        raise InvalidInputError(f"{context} payload is missing required fields: {missing_list}")
    reject_unknown_keys(payload, required, context=context)


def reject_unknown_keys(
    payload: Mapping[str, object],
    allowed: set[str],
    *,
    context: str,
) -> None:
    unknown_keys = set(payload) - allowed
    if unknown_keys:
        unknown_list = ", ".join(sorted(unknown_keys))
        raise InvalidInputError(f"{context} payload has unknown fields: {unknown_list}")
