from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from minx_mcp.base_service import BaseService
from minx_mcp.db import get_connection


def test_base_service_db_path_property(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    get_connection(db_path).close()
    svc = BaseService(db_path)
    assert svc.db_path == db_path


def test_base_service_conn_cached_per_thread(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    get_connection(db_path).close()
    svc = BaseService(db_path)
    assert svc.conn is svc.conn


def test_base_service_distinct_connections_per_thread(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    get_connection(db_path).close()
    svc = BaseService(db_path)
    found: dict[str, object] = {}

    def capture() -> None:
        found[threading.current_thread().name] = svc.conn

    t1 = threading.Thread(target=capture, name="t-one")
    t2 = threading.Thread(target=capture, name="t-two")
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert found["t-one"] is not found["t-two"]
    svc.close()


def test_base_service_close_reopens_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    get_connection(db_path).close()
    svc = BaseService(db_path)
    first = svc.conn
    svc.close()
    second = svc.conn
    assert first is not second
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")
    assert second.execute("SELECT 1").fetchone() is not None
    svc.close()


def test_base_service_context_manager_closes(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    get_connection(db_path).close()
    with BaseService(db_path) as svc:
        assert svc.conn.execute("SELECT 1").fetchone() is not None
    svc2 = BaseService(db_path)
    assert svc2.conn.execute("SELECT 1").fetchone() is not None
    svc2.close()
