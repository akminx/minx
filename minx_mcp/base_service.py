from __future__ import annotations

import contextvars
import threading
from pathlib import Path
from sqlite3 import Connection
from typing import Self

from minx_mcp.db import get_connection


class BaseService:
    """Base class for domain services.

    Each instance owns a :mod:`contextvars` ContextVar scoped to its ``id()``,
    so concurrent asyncio tasks sharing the same service instance each get
    their own SQLite connection. This was previously a ``threading.local``,
    which granted only per-OS-thread isolation — on an asyncio event loop
    that meant all concurrent coroutines shared one physical connection
    and could interleave ``BEGIN IMMEDIATE`` / ``COMMIT`` pairs, corrupting
    transactional state.

    Synchronous code (tests, scripts, the CLI) still works the same way:
    outside an asyncio task, every call inside a single context sees the
    same connection until :meth:`close` is called.

    ``threading.local()`` on :attr:`_local` is retained for subclasses (for
    example ``FinanceService``) that cache small per-thread values next to
    the DB handle. The SQLite connection itself is not stored on
    :attr:`_local` under normal construction; it lives in :attr:`_conn_var`.
    ``MealsService.from_connection`` bypasses :meth:`__init__` and primes
    ``_local.conn``; the first read of :attr:`conn` copies that handle into
    the ContextVar for this context.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._local = threading.local()
        # Name must be unique per instance so contextvars does not collapse
        # connections across unrelated service objects sharing the same class.
        self._conn_var: contextvars.ContextVar[Connection | None] = contextvars.ContextVar(
            f"_base_service_conn_{id(self)}",
            default=None,
        )

    def _ensure_conn_var(self) -> contextvars.ContextVar[Connection | None]:
        """Lazily allocate :attr:`_conn_var` when ``__init__`` was skipped (e.g. ``from_connection``)."""
        var = getattr(self, "_conn_var", None)
        if var is None:
            self._conn_var = contextvars.ContextVar(
                f"_base_service_conn_{id(self)}",
                default=None,
            )
            return self._conn_var
        return var

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def conn(self) -> Connection:
        var = self._ensure_conn_var()
        conn = var.get()
        if conn is None:
            local = getattr(self, "_local", None)
            if local is not None:
                legacy = getattr(local, "conn", None)
                if legacy is not None:
                    var.set(legacy)
                    return legacy
            conn = get_connection(self._db_path)
            var.set(conn)
        return conn

    def close(self) -> None:
        var = getattr(self, "_conn_var", None)
        if var is not None:
            conn = var.get()
            if conn is not None:
                conn.close()
                var.set(None)
        local = getattr(self, "_local", None)
        if local is not None and getattr(local, "conn", None) is not None:
            local.conn = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
