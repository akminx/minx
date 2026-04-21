from __future__ import annotations

import asyncio
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
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - best-effort teardown; don't mask test results
            pass
    for loop in created_loops:
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:  # noqa: BLE001
            pass

    # Belt-and-braces sweep for event loops that bypassed our ``new_event_loop``
    # hook (e.g. created via ``EventLoopPolicy.new_event_loop()`` directly, or
    # constructed before this fixture started patching — common with anyio /
    # mcp stdio_client transports). Without this, the loops are only collected
    # when the GC feels like it and surface as a session-level
    # ``PytestUnraisableExceptionWarning`` attributed to an unrelated test.
    gc.collect()
    for obj in gc.get_objects():
        if isinstance(obj, asyncio.AbstractEventLoop):
            try:
                if not obj.is_closed():
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
    # Force a collection inside the teardown boundary so any late GC of tracked
    # resources (missed connections, orphaned event loops) is *attributed to the
    # originating test* rather than surfacing later as a session-teardown
    # PytestUnraisableExceptionWarning against an unrelated test.
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
