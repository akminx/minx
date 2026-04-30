from __future__ import annotations

import asyncio
import json
from pathlib import Path

from minx_mcp.core.server import create_core_server
from tests.helpers import MinxTestConfig, list_tool_names, read_resource_text


def test_playbook_registry_uses_namespaced_required_tools_and_allows_cross_server(
    tmp_path: Path,
) -> None:
    server = create_core_server(MinxTestConfig(tmp_path / "m.db", tmp_path / "vault"))
    payload = json.loads(asyncio.run(read_resource_text(server, "playbook://registry")))

    core_tool_names = list_tool_names(server)
    assert isinstance(payload, dict)
    assert "playbooks" in payload
    assert isinstance(payload["playbooks"], list)

    by_id = {entry["id"]: entry for entry in payload["playbooks"]}

    # Spot-check all five playbooks are present and carry at least one expected tool ref.
    assert "daily_review" in by_id
    assert "core.get_daily_snapshot" in by_id["daily_review"]["required_tools"]
    assert "weekly_report" in by_id
    assert "finance.finance_generate_weekly_report" in by_id["weekly_report"]["required_tools"]
    assert "wiki_update" in by_id
    assert "core.vault_replace_section" in by_id["wiki_update"]["required_tools"]
    assert "memory_review" in by_id
    assert "core.get_pending_memory_candidates" in by_id["memory_review"]["required_tools"]
    assert "goal_nudge" in by_id
    assert "core.get_goal_trajectory" in by_id["goal_nudge"]["required_tools"]

    for entry in payload["playbooks"]:
        required = entry["required_tools"]
        assert isinstance(required, list)
        for tool_ref in required:
            assert "." in tool_ref
            namespace, tool_name = tool_ref.split(".", 1)
            assert namespace in {"core", "finance", "meals", "training"}
            if namespace == "core":
                assert tool_name in core_tool_names
