from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from typing import TextIO


def _env_port(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


SERVERS = [
    {"name": "minx-core", "module": "minx_mcp.core", "port": _env_port("MINX_CORE_PORT", 8001)},
    {
        "name": "minx-finance",
        "module": "minx_mcp.finance",
        "port": _env_port("MINX_FINANCE_PORT", 8000),
    },
    {"name": "minx-meals", "module": "minx_mcp.meals", "port": _env_port("MINX_MEALS_PORT", 8002)},
    {
        "name": "minx-training",
        "module": "minx_mcp.training",
        "port": _env_port("MINX_TRAINING_PORT", 8003),
    },
]


def build_launch_commands(
    server_names: list[str],
    *,
    transport: str,
    python_executable: str,
) -> list[list[str]]:
    selected = [server for server in SERVERS if server["name"] in server_names]
    unknown = sorted(set(server_names) - {str(server["name"]) for server in SERVERS})
    if unknown:
        raise ValueError(f"unknown server(s): {', '.join(unknown)}")
    if not selected:
        raise ValueError("at least one server must be selected")
    if transport == "stdio" and len(selected) != 1:
        raise ValueError("stdio transport supports exactly one server")
    if transport not in {"stdio", "http"}:
        raise ValueError(f"unsupported transport: {transport}")

    commands: list[list[str]] = []
    for server in selected:
        command = [
            python_executable,
            "-m",
            str(server["module"]),
            "--transport",
            transport,
        ]
        if transport == "http":
            command.extend(["--port", str(server["port"])])
        commands.append(command)
    return commands


def launch_commands(
    commands: list[list[str]],
    *,
    poll_interval: float = 0.1,
    log_stream: TextIO | None = None,
) -> int:
    log_stream = log_stream or sys.stderr
    procs: list[subprocess.Popen[bytes]] = []

    def _shutdown(signum: int, frame: object) -> None:
        del frame
        _terminate_all(procs)
        raise SystemExit(128 + signum)

    previous_sigint = signal.signal(signal.SIGINT, _shutdown)
    previous_sigterm = signal.signal(signal.SIGTERM, _shutdown)
    try:
        for command in commands:
            proc = subprocess.Popen(command)
            procs.append(proc)
            print(f"Started {' '.join(command)} (pid {proc.pid})", file=log_stream)
        return _wait_for_processes(procs, poll_interval=poll_interval)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


def _wait_for_processes(
    procs: list[subprocess.Popen[bytes]],
    *,
    poll_interval: float,
) -> int:
    pending = list(procs)
    while pending:
        for proc in list(pending):
            returncode = proc.poll()
            if returncode is None:
                continue
            pending.remove(proc)
            if returncode != 0:
                _terminate_all(pending)
                return returncode
        if pending:
            time.sleep(poll_interval)
    return 0


def _terminate_all(procs: list[subprocess.Popen[bytes]]) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in procs:
        proc.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Minx MCP launcher")
    parser.add_argument("--servers", nargs="*", default=[server["name"] for server in SERVERS])
    parser.add_argument("--transport", choices=["stdio", "http"], default="http")
    args = parser.parse_args()
    try:
        commands = build_launch_commands(
            [str(server_name) for server_name in args.servers],
            transport=str(args.transport),
            python_executable=sys.executable,
        )
    except ValueError as exc:
        parser.error(str(exc))
    raise SystemExit(launch_commands(commands))


if __name__ == "__main__":
    main()
