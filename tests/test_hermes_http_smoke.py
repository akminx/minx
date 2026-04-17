from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from minx_mcp.db import get_connection

SERVERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("minx-core", "minx_mcp.core", ("get_daily_snapshot", "goal_create", "persist_note")),
    (
        "minx-finance",
        "minx_mcp.finance",
        ("safe_finance_summary", "finance_query", "finance_import"),
    ),
    ("minx-meals", "minx_mcp.meals", ("meal_log", "nutrition_profile_get", "pantry_list")),
    (
        "minx-training",
        "minx_mcp.training",
        ("training_session_log", "training_exercise_upsert", "training_progress_summary"),
    ),
)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_hermes_http_stack_smoke(tmp_path: Path) -> None:
    db_path = tmp_path / "minx.db"
    vault_path = tmp_path / "vault"
    staging_path = tmp_path / "staging"
    vault_path.mkdir()
    staging_path.mkdir()
    get_connection(db_path).close()

    ports = {name: _free_port() for name, _, _ in SERVERS}
    procs: list[tuple[str, subprocess.Popen[bytes], Path]] = []
    try:
        for name, module, _ in SERVERS:
            proc, log_path = _start_server(
                module=module,
                port=ports[name],
                db_path=db_path,
                vault_path=vault_path,
                staging_path=staging_path,
                cwd=Path(__file__).resolve().parent.parent,
                log_dir=tmp_path,
            )
            procs.append((name, proc, log_path))

        urls = {name: f"http://127.0.0.1:{ports[name]}/mcp" for name, _, _ in SERVERS}
        for name, _, expected_tools in SERVERS:
            tools = await _wait_for_tools(urls[name], set(expected_tools))
            assert set(expected_tools).issubset(tools)

        review_date = "2026-04-13"
        meal_result = await _call_tool(
            urls["minx-meals"],
            "meal_log",
            {
                "meal_kind": "lunch",
                "occurred_at": f"{review_date}T12:00:00Z",
                "summary": "hermes smoke meal",
                "protein_grams": 34.0,
                "calories": 650,
            },
        )
        assert meal_result["success"] is True
        meal_entry = meal_result["data"]["meal"]
        assert meal_entry["meal_kind"] == "lunch"

        exercise_result = await _call_tool(
            urls["minx-training"],
            "training_exercise_upsert",
            {
                "display_name": "Deadlift",
                "is_compound": True,
            },
        )
        assert exercise_result["success"] is True
        exercise_id = exercise_result["data"]["exercise"]["id"]

        session_result = await _call_tool(
            urls["minx-training"],
            "training_session_log",
            {
                "occurred_at": f"{review_date}T08:00:00Z",
                "sets": [
                    {
                        "exercise_id": exercise_id,
                        "reps": 5,
                        "weight_kg": 145.0,
                    }
                ],
            },
        )
        assert session_result["success"] is True
        assert session_result["data"]["session"]["set_count"] == 1
        assert session_result["data"]["session"]["total_volume_kg"] == pytest.approx(725.0)

        snapshot_result = await _call_tool(
            urls["minx-core"],
            "get_daily_snapshot",
            {"review_date": review_date, "force": False},
        )
        assert snapshot_result["success"] is True
        snapshot = snapshot_result["data"]
        assert snapshot["date"] == review_date
        assert snapshot["nutrition"] is not None
        assert snapshot["nutrition"]["meal_count"] == 1
        assert snapshot["nutrition"]["protein_grams"] == pytest.approx(34.0)
        assert snapshot["training"] is not None
        assert snapshot["training"]["sessions_logged"] == 1
        assert snapshot["training"]["total_sets"] == 1
    finally:
        _terminate_processes(procs)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(
    *,
    module: str,
    port: int,
    db_path: Path,
    vault_path: Path,
    staging_path: Path,
    cwd: Path,
    log_dir: Path,
) -> tuple[subprocess.Popen[bytes], Path]:
    env = os.environ.copy()
    env["MINX_DB_PATH"] = str(db_path)
    env["MINX_VAULT_PATH"] = str(vault_path)
    env["MINX_STAGING_PATH"] = str(staging_path)
    env["MINX_HTTP_HOST"] = "127.0.0.1"

    log_path = log_dir / f"{module.rsplit('.', 1)[-1]}.log"
    log_file = log_path.open("ab")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                module,
                "--transport",
                "http",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        log_file.close()
        raise

    log_file.close()
    return proc, log_path


async def _wait_for_tools(url: str, expected_tools: set[str], timeout: float = 30.0) -> set[str]:
    deadline = asyncio.get_running_loop().time() + timeout
    last_error: Exception | None = None

    while True:
        try:
            async with (
                httpx.AsyncClient(timeout=5.0) as http_client,
                streamable_http_client(url, http_client=http_client) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                tools_result = await session.list_tools()
                tool_names = {tool.name for tool in tools_result.tools}
                if expected_tools.issubset(tool_names):
                    return tool_names
                raise AssertionError(
                    f"{url} missing expected tools: {sorted(expected_tools - tool_names)}"
                )
        except Exception as exc:  # noqa: BLE001 - retry until the server is ready
            last_error = exc
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError(f"timed out waiting for {url}: {exc}") from exc
            await asyncio.sleep(0.2)

        if last_error is not None and asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"timed out waiting for {url}: {last_error}") from last_error


async def _call_tool(url: str, name: str, arguments: dict[str, object]) -> object:
    async with (
        httpx.AsyncClient(timeout=5.0) as http_client,
        streamable_http_client(
            url,
            http_client=http_client,
        ) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(name, arguments)
        assert result.isError is False
        assert result.structuredContent is not None
        return result.structuredContent


def _terminate_processes(procs: list[tuple[str, subprocess.Popen[bytes], Path]]) -> None:
    for _, proc, _ in procs:
        if proc.poll() is None:
            proc.terminate()

    for _, proc, _ in procs:
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        if proc.poll() is None:
            proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
