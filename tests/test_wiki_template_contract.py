from __future__ import annotations

import asyncio
from pathlib import Path

from minx_mcp.core.server import create_core_server
from tests.helpers import MinxTestConfig, get_tool, read_resource_text


def test_non_memory_wiki_templates_keep_minx_wiki_frontmatter(tmp_path: Path) -> None:
    server = create_core_server(MinxTestConfig(tmp_path / "m.db", tmp_path / "vault"))
    for name in ("entity", "pattern", "review", "goal"):
        template = asyncio.run(read_resource_text(server, f"wiki-templates://{name}"))
        assert "type: minx-wiki" in template
        assert "type: minx-memory" not in template


def test_reconciler_ignores_minx_wiki_notes(tmp_path: Path) -> None:
    db_path = tmp_path / "m.db"
    vault_path = tmp_path / "vault"
    server = create_core_server(MinxTestConfig(db_path, vault_path))

    for name in ("entity", "pattern", "review", "goal"):
        note = vault_path / "Minx" / "Wiki" / f"{name}.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            (
                "---\n"
                "type: minx-wiki\n"
                f"wiki_type: {name}\n"
                "---\n\n"
                f"# {name}\n\n"
                "## Summary\n\n"
                "placeholder\n"
            ),
            encoding="utf-8",
        )

    result = get_tool(server, "vault_reconcile_memories").fn(False)
    assert result["success"] is True
    report = result["data"]["report"]
    assert report["scanned"] == 0
