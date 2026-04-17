from __future__ import annotations

import threading
from pathlib import Path
from sqlite3 import Connection
from typing import Self

from minx_mcp.db import get_connection


class BaseService:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._local = threading.local()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def conn(self) -> Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = get_connection(self._db_path)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
