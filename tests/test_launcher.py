from __future__ import annotations

import sys

import pytest

from minx_mcp import launcher
from minx_mcp.launcher import build_launch_commands, launch_commands


class _FakeProcess:
    def __init__(self, returncode: int | None, *, pid: int = 123) -> None:
        self.returncode = returncode
        self.pid = pid
        self.terminated = False
        self.waited = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.waited = True
        return self.returncode or 0


def test_launcher_builds_distinct_http_commands_for_all_servers() -> None:
    commands = build_launch_commands(
        ["minx-core", "minx-finance", "minx-meals", "minx-training"],
        transport="http",
        python_executable=sys.executable,
    )

    assert commands == [
        [
            sys.executable,
            "-m",
            "minx_mcp.core",
            "--transport",
            "http",
            "--port",
            "8001",
        ],
        [
            sys.executable,
            "-m",
            "minx_mcp.finance",
            "--transport",
            "http",
            "--port",
            "8000",
        ],
        [
            sys.executable,
            "-m",
            "minx_mcp.meals",
            "--transport",
            "http",
            "--port",
            "8002",
        ],
        [
            sys.executable,
            "-m",
            "minx_mcp.training",
            "--transport",
            "http",
            "--port",
            "8003",
        ],
    ]


def test_launcher_rejects_multi_server_stdio() -> None:
    with pytest.raises(ValueError, match="stdio transport supports exactly one server"):
        build_launch_commands(
            ["minx-core", "minx-finance"],
            transport="stdio",
            python_executable=sys.executable,
        )


def test_launcher_allows_single_server_stdio() -> None:
    assert build_launch_commands(
        ["minx-meals"],
        transport="stdio",
        python_executable=sys.executable,
    ) == [
        [
            sys.executable,
            "-m",
            "minx_mcp.meals",
            "--transport",
            "stdio",
        ]
    ]


def test_launcher_logs_to_stderr_not_stdout(monkeypatch, capsys) -> None:
    process = _FakeProcess(0)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda command: process)

    exit_code = launch_commands([["python", "-m", "minx_mcp.meals"]], poll_interval=0)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "Started python -m minx_mcp.meals" in captured.err


def test_launcher_nonzero_child_exit_terminates_peers(monkeypatch) -> None:
    failed = _FakeProcess(1, pid=1)
    peer = _FakeProcess(None, pid=2)
    processes = iter([failed, peer])
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda command: next(processes))

    exit_code = launch_commands(
        [["python", "-m", "minx_mcp.core"], ["python", "-m", "minx_mcp.meals"]],
        poll_interval=0,
    )

    assert exit_code == 1
    assert peer.terminated is True
    assert peer.waited is True
