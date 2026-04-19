from __future__ import annotations

import asyncio
import json
from pathlib import Path

from minx_mcp.core.server import create_core_server
from tests.helpers import MinxTestConfig


async def _read_resource(server, uri: str) -> str:
    resource = await server._resource_manager.get_resource(uri)
    return await resource.read()


def test_playbook_registry_uses_namespaced_required_tools_and_allows_cross_server(
    tmp_path: Path,
) -> None:
    server = create_core_server(MinxTestConfig(tmp_path / "m.db", tmp_path / "vault"))
    payload = json.loads(asyncio.run(_read_resource(server, "playbook://registry")))

    core_tool_names = {tool.name for tool in server._tool_manager.list_tools()}
    assert isinstance(payload, dict)
    assert "playbooks" in payload
    assert isinstance(payload["playbooks"], list)

    by_id = {entry["id"]: entry for entry in payload["playbooks"]}
    assert "weekly_report" in by_id
    assert "finance.finance_generate_weekly_report" in by_id["weekly_report"]["required_tools"]

    for entry in payload["playbooks"]:
        required = entry["required_tools"]
        assert isinstance(required, list)
        for tool_ref in required:
            assert "." in tool_ref
            namespace, tool_name = tool_ref.split(".", 1)
            assert namespace in {"core", "finance", "meals", "training"}
            if namespace == "core":
                assert tool_name in core_tool_names
