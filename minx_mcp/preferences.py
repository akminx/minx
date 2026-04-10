from __future__ import annotations

import json
from sqlite3 import Connection


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
    row = conn.execute(
        "SELECT value_json FROM preferences WHERE domain = ? AND key = ?",
        (domain, key),
    ).fetchone()
    return json.loads(row["value_json"]) if row else default


def save_csv_mapping(conn: Connection, profile_name: str, mapping: dict[str, object]) -> None:
    set_preference(conn, "finance.csv_mapping", profile_name, mapping)


def get_csv_mapping(conn: Connection, profile_name: str) -> dict[str, object] | None:
    mapping = get_preference(conn, "finance.csv_mapping", profile_name, None)
    return mapping if isinstance(mapping, dict) else None


def get_finance_anomaly_threshold_cents(conn: Connection) -> int:
    threshold = get_preference(conn, "finance", "anomaly_threshold_cents", -25_000)
    if isinstance(threshold, bool):
        return int(threshold)
    if isinstance(threshold, int):
        return threshold
    if isinstance(threshold, float):
        return int(threshold)
    if isinstance(threshold, str):
        return int(threshold)
    return -25_000
