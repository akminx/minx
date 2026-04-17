from __future__ import annotations

import asyncio
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
    """Stable connection within one sync context (ContextVar default context).

    Name predates ContextVar migration; behavior is per-context, not per OS thread.
    """
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


@pytest.mark.asyncio
async def test_conn_returns_distinct_connections_in_concurrent_asyncio_tasks(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    get_connection(db_path).close()
    service = BaseService(db_path)
    try:
        barrier = asyncio.Event()

        async def use_conn() -> int:
            # Force connection creation then yield so a sibling task can interleave.
            c = service.conn
            await barrier.wait()
            return id(c)

        barrier_trigger = asyncio.create_task(asyncio.sleep(0.05))
        t1 = asyncio.create_task(use_conn())
        t2 = asyncio.create_task(use_conn())
        await barrier_trigger
        barrier.set()
        ids = await asyncio.gather(t1, t2)
        assert ids[0] != ids[1], "each task must get its own connection"
    finally:
        service.close()


@pytest.mark.asyncio
async def test_conn_is_stable_within_a_single_asyncio_task(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    get_connection(db_path).close()
    service = BaseService(db_path)
    try:
        c1 = service.conn
        await asyncio.sleep(0.01)
        c2 = service.conn
        assert c1 is c2
    finally:
        service.close()


@pytest.mark.asyncio
async def test_conn_does_not_leak_across_sibling_tasks_via_parent_setting(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    get_connection(db_path).close()
    service = BaseService(db_path)
    try:
        parent_conn = service.conn  # sets the contextvar in parent task
        parent_id = id(parent_conn)

        async def sibling() -> int:
            # Sibling inherits the parent's conn snapshot but mutations stay local.
            inherited = service.conn
            service.close()  # force new connection in this task
            fresh = service.conn
            assert id(inherited) == parent_id, "child inherits parent's conn at spawn"
            assert id(fresh) != parent_id, "child's close+reopen is isolated"
            return id(fresh)

        sibling_ids = await asyncio.gather(sibling(), sibling())
        # Both siblings produced their own fresh conns, distinct from parent and each other.
        assert sibling_ids[0] != parent_id
        assert sibling_ids[1] != parent_id
        assert sibling_ids[0] != sibling_ids[1]
    finally:
        service.close()


def test_sync_usage_still_works(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    get_connection(db_path).close()
    service = BaseService(db_path)
    try:
        c1 = service.conn
        c2 = service.conn
        assert c1 is c2
        assert c1.execute("SELECT 1").fetchone() is not None
        service.close()
        c3 = service.conn
        assert c3 is not c1
        assert c3.execute("SELECT 1").fetchone() is not None
    finally:
        service.close()


def test_close_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    get_connection(db_path).close()
    service = BaseService(db_path)
    _ = service.conn
    service.close()
    service.close()
    service.close()


def test_two_service_instances_have_independent_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "t.db"
    get_connection(db_path).close()
    a = BaseService(db_path)
    b = BaseService(db_path)
    try:
        assert id(a.conn) != id(b.conn)
        a.close()
        assert b.conn.execute("SELECT 1").fetchone() is not None
    finally:
        a.close()
        b.close()
