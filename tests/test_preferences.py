import logging

from minx_mcp.db import get_connection
from minx_mcp.preferences import (
    get_csv_mapping,
    get_finance_anomaly_threshold_cents,
    get_preference,
    save_csv_mapping,
)


def test_csv_mapping_round_trip(tmp_path):
    conn = get_connection(tmp_path / "minx.db")

    save_csv_mapping(
        conn,
        "generic-checking",
        {
            "account_name": "DCU",
            "date_column": "Date",
            "amount_column": "Amount",
            "description_column": "Memo",
            "date_format": "%Y-%m-%d",
        },
    )

    loaded = get_csv_mapping(conn, "generic-checking")

    assert loaded["account_name"] == "DCU"
    assert loaded["description_column"] == "Memo"


def test_get_preference_returns_default_on_malformed_json(tmp_path, caplog):
    conn = get_connection(tmp_path / "minx.db")
    conn.execute(
        "INSERT INTO preferences (domain, key, value_json) VALUES ('core', 'timezone', '{bad')"
    )
    conn.commit()

    with caplog.at_level(logging.WARNING):
        value = get_preference(conn, "core", "timezone", "UTC")

    assert value == "UTC"
    assert "core.timezone" in caplog.text


def test_get_finance_anomaly_threshold_cents_returns_default_on_non_int(tmp_path, caplog):
    conn = get_connection(tmp_path / "minx.db")
    conn.execute(
        """
        INSERT INTO preferences (domain, key, value_json)
        VALUES ('finance', 'anomaly_threshold_cents', '"not-a-number"')
        """
    )
    conn.commit()

    with caplog.at_level(logging.WARNING):
        value = get_finance_anomaly_threshold_cents(conn)

    assert value == -25_000
    assert "finance.anomaly_threshold_cents" in caplog.text
