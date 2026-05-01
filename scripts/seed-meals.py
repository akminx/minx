#!/usr/bin/env python3
"""Bulk-load meals from a JSON or YAML file via the minx-meals MCP server.

Input file: a list of meal dicts. Each dict must include `meal_kind` and
`occurred_at` (ISO 8601). Optional: `summary`, `food_items`, `protein_grams`,
`calories`.

Example meals.json:
[
  {"meal_kind": "breakfast", "occurred_at": "2026-04-30T08:00:00",
   "summary": "oatmeal + protein shake", "protein_grams": 35, "calories": 450},
  {"meal_kind": "lunch", "occurred_at": "2026-04-30T12:30:00",
   "summary": "chicken bowl", "protein_grams": 48, "calories": 720}
]

Usage:
    uv run scripts/seed-meals.py meals.json
    uv run scripts/seed-meals.py meals.yaml

Requires the minx-meals MCP server to be running (start_hermes_stack.sh).
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
    port = os.environ.get("MINX_MEALS_PORT", "8002")
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
        print("error: input file must be a list of meal dicts", file=sys.stderr)
        sys.exit(2)
    return data


async def _run(meals: list[dict[str, object]]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    async with streamablehttp_client(_endpoint()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for i, meal in enumerate(meals):
                if "meal_kind" not in meal or "occurred_at" not in meal:
                    print(f"error: meal #{i} missing meal_kind/occurred_at", file=sys.stderr)
                    sys.exit(2)
                response = await session.call_tool("meal_log", meal)
                results.append(response.structuredContent or {})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Path to JSON or YAML file with a list of meals")
    args = parser.parse_args()

    path = Path(args.source).expanduser()
    if not path.exists():
        print(f"error: source not found: {path}", file=sys.stderr)
        return 2

    meals = _load(path)
    results = asyncio.run(_run(meals))
    print(json.dumps({"logged": len(results), "results": results}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
