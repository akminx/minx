from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from minx_mcp.time_utils import format_utc_timestamp, normalize_utc_timestamp, utc_now_isoformat

logger = logging.getLogger(__name__)


class EventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransactionsImportedPayload(EventPayload):
    account_name: str
    account_id: int
    job_id: str
    transaction_count: int
    total_cents: int
    source_kind: str


class TransactionsCategorizedPayload(EventPayload):
    count: int
    categories: list[str]


class ReportGeneratedPayload(EventPayload):
    report_type: Literal["weekly", "monthly"]
    period_start: str
    period_end: str
    vault_path: str


class AnomaliesDetectedPayload(EventPayload):
    count: int
    total_cents: int


PAYLOAD_MODELS: dict[str, type[EventPayload]] = {
    "finance.transactions_imported": TransactionsImportedPayload,
    "finance.transactions_categorized": TransactionsCategorizedPayload,
    "finance.report_generated": ReportGeneratedPayload,
    "finance.anomalies_detected": AnomaliesDetectedPayload,
}


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
) -> int | None:
    try:
        model = PAYLOAD_MODELS.get(event_type)
        if model is None:
            logger.warning("Unknown event type %s; event not emitted", event_type)
            return None

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
    except ValidationError as exc:
        logger.warning(
            "Event payload validation failed for %s: %s",
            event_type,
            exc,
        )
        return None
    except Exception:
        logger.exception("Event emission failed for %s", event_type)
        return None


def query_events(
    db: sqlite3.Connection,
    domain: str | None = None,
    event_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    timezone: str | None = None,
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
            payload=json.loads(row["payload"]),
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
    end_utc = (
        _local_date_to_utc_boundary(end, zone, add_days=1) if end is not None else None
    )
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


