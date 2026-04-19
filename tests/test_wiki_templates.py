from __future__ import annotations

import asyncio
import json
from pathlib import Path

from minx_mcp.core.server import create_core_server
from tests.helpers import MinxTestConfig


async def _read_resource(server, uri: str) -> str:
    resource = await server._resource_manager.get_resource(uri)
    return await resource.read()


def test_wiki_template_resources_include_memory_and_human_edit_sections(tmp_path: Path) -> None:
    server = create_core_server(MinxTestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    names = json.loads(asyncio.run(_read_resource(server, "wiki-templates://list")))

    assert names == ["entity", "pattern", "review", "goal", "memory"]
    for name in names:
        template = asyncio.run(_read_resource(server, f"wiki-templates://{name}"))
        assert "## Summary" in template
        assert "## Human Editable" in template
        assert "## System Metadata" in template
    memory = asyncio.run(_read_resource(server, "wiki-templates://memory"))
    assert "type: minx-memory" in memory
    assert "memory_key: ${memory_key}" in memory
    assert "payload_json: '${payload_json}'" in memory


def test_existing_wiki_templates_keep_minx_wiki_type(tmp_path: Path) -> None:
    server = create_core_server(MinxTestConfig(tmp_path / "minx.db", tmp_path / "vault"))

    for name in ("entity", "pattern", "review", "goal"):
        template = asyncio.run(_read_resource(server, f"wiki-templates://{name}"))
        assert "type: minx-wiki" in template
        assert "type: minx-memory" not in template
