#!/usr/bin/env python3
"""Bulk-load training sessions from a JSON or YAML file via minx-training MCP.

Input file: a list of session dicts. Each must include `occurred_at` (ISO 8601)
and `sets` (list of set dicts). Optional: `program_id`, `notes`.

Example sessions.json:
[
  {
    "occurred_at": "2026-04-29T17:30:00",
    "notes": "push day",
    "sets": [
      {"exercise": "Bench Press", "weight_kg": 80, "reps": 5},
      {"exercise": "Bench Press", "weight_kg": 80, "reps": 5},
      {"exercise": "Overhead Press", "weight_kg": 50, "reps": 6}
    ]
  }
]

Usage:
    uv run scripts/seed-training.py sessions.json
    uv run scripts/seed-training.py sessions.yaml

Requires the minx-training MCP server to be running (start_hermes_stack.sh).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def _endpoint() -> str:
    host = os.environ.get("MINX_HTTP_HOST", "127.0.0.1")
    port = os.environ.get("MINX_TRAINING_PORT", "8003")
    return f"http://{host}:{port}/mcp"


def _load(path: Path) -> list[dict[str, object]]:
    text = path.read_text()
    if path.suffix in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            print("error: install pyyaml to load YAML input", file=sys.stderr)
            sys.exit(2)
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, list):
        print("error: input file must be a list of session dicts", file=sys.stderr)
        sys.exit(2)
    return data


async def _run(sessions: list[dict[str, object]]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    async with streamablehttp_client(_endpoint()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for i, item in enumerate(sessions):
                if "occurred_at" not in item or "sets" not in item:
                    print(f"error: session #{i} missing occurred_at/sets", file=sys.stderr)
                    sys.exit(2)
                response = await session.call_tool("training_session_log", item)
                results.append(response.structuredContent or {})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Path to JSON or YAML file with a list of training sessions")
    args = parser.parse_args()

    path = Path(args.source).expanduser()
    if not path.exists():
        print(f"error: source not found: {path}", file=sys.stderr)
        return 2

    sessions = _load(path)
    results = asyncio.run(_run(sessions))
    print(json.dumps({"logged": len(results), "results": results}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
