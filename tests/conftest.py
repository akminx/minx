from __future__ import annotations
import pytest
from pathlib import Path
from minx_mcp.db import get_connection
from tests.helpers import FinanceSeeder

@pytest.fixture
def db_conn(tmp_path):
    conn = get_connection(tmp_path / "minx.db")
    yield conn
    conn.close()

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "minx.db"

@pytest.fixture
def seeder(db_conn):
    return FinanceSeeder(db_conn)
