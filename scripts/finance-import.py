#!/usr/bin/env python3
"""Import a finance statement via the running minx-finance MCP server.

Usage:
    uv run scripts/finance-import.py <csv-path> --account "DCU"
    uv run scripts/finance-import.py <csv-path> --account "DCU" --commit
    uv run scripts/finance-import.py <csv-path> --account "DCU" --source-kind chase_csv

Without --commit, runs finance_import_preview (no DB writes). With --commit,
runs finance_import. Either form prints the structured response as JSON.

Requires the minx-finance MCP server to be running (start_hermes_stack.sh).
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
    port = os.environ.get("MINX_FINANCE_PORT", "8000")
    return f"http://{host}:{port}/mcp"


async def _run(
    *,
    source_ref: str,
    account_name: str,
    source_kind: str | None,
    commit: bool,
) -> dict[str, object]:
    tool = "finance_import" if commit else "finance_import_preview"
    args: dict[str, object] = {"source_ref": source_ref, "account_name": account_name}
    if source_kind:
        args["source_kind"] = source_kind
    async with streamablehttp_client(_endpoint()) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            return result.structuredContent or {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Path to CSV/statement file")
    parser.add_argument("--account", required=True, help="Account display name (e.g. 'DCU')")
    parser.add_argument(
        "--source-kind",
        default=None,
        help="Optional source kind hint (e.g. chase_csv). Server auto-detects if omitted.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually write to DB. Without this flag, runs preview only.",
    )
    args = parser.parse_args()

    source_path = Path(args.source).expanduser()
    if not source_path.exists():
        print(f"error: source not found: {source_path}", file=sys.stderr)
        return 2

    payload = asyncio.run(
        _run(
            source_ref=str(source_path),
            account_name=args.account,
            source_kind=args.source_kind,
            commit=args.commit,
        )
    )
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
