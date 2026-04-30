from __future__ import annotations

import asyncio
import contextlib
import gc
import sqlite3

import pytest

from minx_mcp.db import get_connection
from tests.helpers import FinanceSeeder, MealsSeeder, MinxTestConfig


@pytest.fixture(autouse=True)
def _autoclose_leaked_resources(monkeypatch: pytest.MonkeyPatch):
    """Close every ``sqlite3.Connection`` and ``asyncio`` event loop opened during a test.

    Background: a mix of ad-hoc tests call ``get_connection(...)`` or create
    event loops (directly or through ``asyncio.run``) without a guaranteed
    ``close()``. Without this fixture the garbage collector eventually reaps
    them; ``__del__`` then raises, pytest upgrades that to a
    ``PytestUnraisableExceptionWarning``, and any non-default ``-W error``
    configuration (or ``filterwarnings = ["error"]`` in pyproject) starts
    failing unrelated tests at session teardown.

    We monkeypatch ``sqlite3.connect`` and ``asyncio.new_event_loop`` at the
    module level (not ``minx_mcp.db.get_connection``) because tests and
    production modules bind ``get_connection`` at import time; patching the
    function object wouldn't reach them. These helpers call ``sqlite3.connect``
    / ``asyncio.new_event_loop`` by attribute lookup each time, so this patch
    is effective.

    Tests that already close their resources are unaffected —
    ``Connection.close()`` and ``loop.close()`` are both idempotent.
    """
    created_conns: list[sqlite3.Connection] = []
    created_loops: list[asyncio.AbstractEventLoop] = []

    original_connect = sqlite3.connect
    original_new_loop = asyncio.new_event_loop

    def tracking_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        created_conns.append(conn)
        return conn

    def tracking_new_loop(*args, **kwargs):
        loop = original_new_loop(*args, **kwargs)
        created_loops.append(loop)
        return loop

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    # Patch both the top-level alias and the underlying ``asyncio.events`` symbol:
    # ``asyncio.Runner`` (used by ``asyncio.run`` and pytest-asyncio) calls
    # ``events.new_event_loop()`` via attribute lookup on ``asyncio.events``.
    monkeypatch.setattr(asyncio, "new_event_loop", tracking_new_loop)
    monkeypatch.setattr(asyncio.events, "new_event_loop", tracking_new_loop)

    yield

    for conn in created_conns:
        with contextlib.suppress(Exception):
            conn.close()
    for loop in created_loops:
        with contextlib.suppress(Exception):
            if not loop.is_closed():
                loop.close()

    # Some library code creates loops through lower-level policy hooks that do
    # not pass through the patched constructors above. Close only idle loops so
    # we keep teardown deterministic without touching any still-running loop.
    gc.collect()
    for obj in gc.get_objects():
        if isinstance(obj, asyncio.AbstractEventLoop):
            with contextlib.suppress(Exception):
                if not obj.is_running() and not obj.is_closed():
                    obj.close()
    gc.collect()


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


@pytest.fixture
def meals_seeder(db_conn):
    return MealsSeeder(db_conn)


@pytest.fixture
def test_config(tmp_path):
    return MinxTestConfig(tmp_path / "minx.db", tmp_path / "vault")
