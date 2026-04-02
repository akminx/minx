from minx_mcp.db import get_connection
from minx_mcp.preferences import get_csv_mapping, save_csv_mapping


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
