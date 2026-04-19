from __future__ import annotations

import json
import logging
from sqlite3 import Connection

logger = logging.getLogger(__name__)


def set_preference(conn: Connection, domain: str, key: str, value: object) -> None:
    conn.execute(
        """
        INSERT INTO preferences (domain, key, value_json, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(domain, key)
        DO UPDATE SET value_json = excluded.value_json, updated_at = datetime('now')
        """,
        (domain, key, json.dumps(value)),
    )
    conn.commit()


def get_preference(
    conn: Connection,
    domain: str,
    key: str,
    default: object | None = None,
) -> object | None:
    """Read a JSON preference value, returning ``default`` for missing or malformed storage."""
    row = conn.execute(
        "SELECT value_json FROM preferences WHERE domain = ? AND key = ?",
        (domain, key),
    ).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value_json"])
    except json.JSONDecodeError as exc:
        logger.warning(
            "malformed preference JSON for %s.%s; using default (%s)",
            domain,
            key,
            type(exc).__name__,
        )
        return default


def save_csv_mapping(conn: Connection, profile_name: str, mapping: dict[str, object]) -> None:
    set_preference(conn, "finance.csv_mapping", profile_name, mapping)


def get_csv_mapping(conn: Connection, profile_name: str) -> dict[str, object] | None:
    mapping = get_preference(conn, "finance.csv_mapping", profile_name, None)
    return mapping if isinstance(mapping, dict) else None


def get_finance_anomaly_threshold_cents(conn: Connection) -> int:
    return _coerce_preference_int(
        conn,
        domain="finance",
        key="anomaly_threshold_cents",
        default=-25_000,
    )


def _coerce_preference_int(conn: Connection, *, domain: str, key: str, default: int) -> int:
    """Coerce an integer preference, logging only the key and error type on malformed data."""
    threshold = get_preference(conn, domain, key, default)
    if isinstance(threshold, bool):
        return int(threshold)
    if isinstance(threshold, int):
        return threshold
    if isinstance(threshold, float):
        return int(threshold)
    if isinstance(threshold, str):
        try:
            return int(threshold)
        except ValueError as exc:
            logger.warning(
                "malformed integer preference for %s.%s; using default (%s)",
                domain,
                key,
                type(exc).__name__,
            )
            return default
    logger.warning(
        "malformed integer preference for %s.%s; using default (%s)",
        domain,
        key,
        type(threshold).__name__,
    )
    return default
