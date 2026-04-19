"""Domain timeline event emission and payload versioning.

This module owns DOMAIN TIMELINE events: user-facing behavioral signals
(`finance.transaction_posted`, `meals.meal_logged`, etc.) with registered Pydantic
payload models and versioned upcasters. It is NOT the memory audit trail. Slice 6's
`memory_events` table records memory-lifecycle operations (`created`, `confirmed`,
`rejected`, `expired`, `payload_updated`, `vault_synced`) as plain SQLite rows keyed
by memory_id. Do not route memory-lifecycle events through `emit_event`; they belong
in `memory_events` (see migration 018).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from minx_mcp.event_payloads import EventPayload
from minx_mcp.finance.events import FINANCE_EVENT_PAYLOADS
from minx_mcp.meals.events import MEALS_EVENT_PAYLOADS
from minx_mcp.time_utils import format_utc_timestamp, normalize_utc_timestamp, utc_now_isoformat
from minx_mcp.training.events import TRAINING_EVENT_PAYLOADS

logger = logging.getLogger(__name__)

PAYLOAD_MODELS: dict[str, type[EventPayload]] = {
    **FINANCE_EVENT_PAYLOADS,
    **MEALS_EVENT_PAYLOADS,
    **TRAINING_EVENT_PAYLOADS,
}

# UPCASTER REGISTRY RULES:
# - Keys in each event's upcaster dict are integer target versions (1-based).
# - Versions must be contiguous integers starting from 1 (no gaps, no duplicates).
# - Each upcaster transforms a payload from version N-1 to version N.
# - All upcasters receive and return plain dicts.
# - Register upcasters before first use (module import time).
PAYLOAD_UPCASTERS: dict[str, dict[int, Callable[[dict[str, Any]], dict[str, Any]]]] = {}


def _validate_upcaster_contiguity(
    registry: dict[str, dict[int, Callable[[dict[str, Any]], dict[str, Any]]]] | None = None,
) -> None:
    """Raise if any upcaster chain has version gaps or duplicates. Defaults to global registry."""
    target = registry if registry is not None else PAYLOAD_UPCASTERS
    for event_type, upcasters in target.items():
        if not upcasters:
            continue
        versions = sorted(upcasters)
        expected = list(range(1, len(versions) + 1))
        if versions != expected:
            raise ValueError(
                f"PAYLOAD_UPCASTERS[{event_type!r}] has non-contiguous version keys "
                f"{versions!r}; expected {expected!r} (must start at 1 with no gaps)"
            )


def _upcast_payload(
    event_type: str, payload: dict[str, Any], schema_version: int
) -> dict[str, Any]:
    """Apply registered upcasters to bring ``payload`` up to the latest schema version.

    Each upcaster keyed at version ``N`` transforms a payload from version
    ``N - 1`` to version ``N``. A payload stored with ``schema_version = S``
    is already at version ``S``, so we only apply upcasters for target
    versions strictly greater than ``S`` (i.e. ``S + 1, S + 2, ...``).

    Upcasters are not required to be idempotent, so applying the upcaster
    keyed at ``S`` to a payload already at version ``S`` is incorrect and
    must be avoided.
    """
    upcasters = PAYLOAD_UPCASTERS.get(event_type)
    if upcasters is None:
        return payload
    current = dict(payload)
    for version in sorted(upcasters):
        if schema_version < version:
            current = upcasters[version](current)
    return current


@dataclass(frozen=True)
class Event:
    id: int
    event_type: str
    domain: str
    occurred_at: str
    recorded_at: str
    entity_ref: str | None
    source: str
    payload: dict[str, Any]
    schema_version: int
    sensitivity: str


class UnknownEventTypeError(ValueError):
    """Raised when an unregistered event type is emitted."""


def emit_event(
    db: sqlite3.Connection,
    event_type: str,
    domain: str,
    occurred_at: str,
    entity_ref: str | None,
    source: str,
    payload: dict[str, Any],
    schema_version: int = 1,
    sensitivity: str = "normal",
    *,
    strict: bool = False,
) -> int | None:
    try:
        model = PAYLOAD_MODELS.get(event_type)
        if model is None:
            raise UnknownEventTypeError(
                f"Unknown event type {event_type!r}; registered types: {sorted(PAYLOAD_MODELS)}"
            )

        validated = model.model_validate(payload)
        normalized_occurred_at = normalize_utc_timestamp(occurred_at)
        payload_json = json.dumps(validated.model_dump(mode="json"))
        recorded_at = utc_now_isoformat()
        cursor = db.execute(
            """
            INSERT INTO events (
                event_type,
                domain,
                occurred_at,
                recorded_at,
                entity_ref,
                source,
                payload,
                schema_version,
                sensitivity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                domain,
                normalized_occurred_at,
                recorded_at,
                entity_ref,
                source,
                payload_json,
                schema_version,
                sensitivity,
            ),
        )
        return cursor.lastrowid
    except UnknownEventTypeError:
        raise
    except ValidationError as exc:
        logger.error(
            "event emission dropped for %s: validation failed",
            event_type,
            extra=_event_drop_extra(event_type, domain, entity_ref, exc),
        )
        if strict:
            raise
        return None
    except sqlite3.IntegrityError as exc:
        logger.error(
            "event emission dropped for %s: integrity error",
            event_type,
            extra=_event_drop_extra(event_type, domain, entity_ref, exc),
        )
        if strict:
            raise
        return None
    except sqlite3.DatabaseError as exc:
        logger.exception(
            "event emission dropped for %s: database error",
            event_type,
            extra=_event_drop_extra(event_type, domain, entity_ref, exc),
        )
        if strict:
            raise
        return None
    except Exception:
        logger.exception(
            "event emission dropped for %s: unexpected error",
            event_type,
            extra={
                "event_type": event_type,
                "domain": domain,
                "entity_ref": entity_ref,
                "error_type": "unexpected",
                "error_code": "UNEXPECTED",
            },
        )
        if strict:
            raise
        return None


def _event_drop_extra(
    event_type: str,
    domain: str,
    entity_ref: str | None,
    exc: Exception,
) -> dict[str, object | None]:
    return {
        "event_type": event_type,
        "domain": domain,
        "entity_ref": entity_ref,
        "error_type": type(exc).__name__,
        "error_code": type(exc).__name__.upper(),
    }


def query_events(
    db: sqlite3.Connection,
    domain: str | None = None,
    event_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    timezone: str | None = None,
    sensitivity: str | None = None,
) -> list[Event]:
    start_utc, end_utc = _normalize_range(start=start, end=end, timezone_name=timezone)

    clauses: list[str] = []
    params: list[str] = []

    if domain is not None:
        clauses.append("domain = ?")
        params.append(domain)
    if event_type is not None:
        clauses.append("event_type = ?")
        params.append(event_type)
    if start_utc is not None:
        clauses.append("occurred_at >= ?")
        params.append(start_utc)
    if end_utc is not None:
        clauses.append("occurred_at < ?")
        params.append(end_utc)
    if sensitivity is not None:
        clauses.append("sensitivity = ?")
        params.append(sensitivity)

    sql = """
        SELECT
            id,
            event_type,
            domain,
            occurred_at,
            recorded_at,
            entity_ref,
            source,
            payload,
            schema_version,
            sensitivity
        FROM events
    """
    if clauses:
        sql = f"{sql} WHERE {' AND '.join(clauses)}"
    sql = f"{sql} ORDER BY occurred_at ASC, id ASC"

    rows = db.execute(sql, params).fetchall()
    return [
        Event(
            id=row["id"],
            event_type=row["event_type"],
            domain=row["domain"],
            occurred_at=row["occurred_at"],
            recorded_at=row["recorded_at"],
            entity_ref=row["entity_ref"],
            source=row["source"],
            payload=_upcast_payload(
                row["event_type"],
                json.loads(row["payload"]),
                row["schema_version"],
            ),
            schema_version=row["schema_version"],
            sensitivity=row["sensitivity"],
        )
        for row in rows
    ]


def _normalize_range(
    *,
    start: str | None,
    end: str | None,
    timezone_name: str | None,
) -> tuple[str | None, str | None]:
    if timezone_name is None:
        return (
            normalize_utc_timestamp(start) if start is not None else None,
            normalize_utc_timestamp(end) if end is not None else None,
        )

    zone = ZoneInfo(timezone_name)
    start_utc = _local_date_to_utc_boundary(start, zone) if start is not None else None
    end_utc = _local_date_to_utc_boundary(end, zone, add_days=1) if end is not None else None
    return start_utc, end_utc


def _local_date_to_utc_boundary(
    value: str,
    zone: ZoneInfo,
    *,
    add_days: int = 0,
) -> str:
    local_day = date.fromisoformat(value) + timedelta(days=add_days)
    local_midnight = datetime.combine(local_day, datetime.min.time(), tzinfo=zone)
    return format_utc_timestamp(local_midnight)


# Run at import time after all module-level upcaster registrations.
_validate_upcaster_contiguity()
