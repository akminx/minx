from __future__ import annotations

from pathlib import Path

from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from tests.helpers import MinxTestConfig, get_tool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server(tmp_path: Path):
    db_path = tmp_path / "minx.db"
    get_connection(db_path).close()
    return create_core_server(MinxTestConfig(db_path, tmp_path / "vault"))


# ---------------------------------------------------------------------------
# Unit tests — delegates to VaultWriter.replace_section
# ---------------------------------------------------------------------------


def test_vault_replace_section_calls_vault_writer(tmp_path: Path) -> None:
    server = _make_server(tmp_path)
    tool = get_tool(server, "vault_replace_section").fn

    vault = tmp_path / "vault"
    note = vault / "Minx" / "notes.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Doc\n\n## Summary\n\nold content\n", encoding="utf-8")

    result = tool("Minx/notes.md", "Summary", "new content")

    assert result["success"] is True
    assert "path" in result["data"]
    assert "notes.md" in result["data"]["path"]
    assert "new content" in note.read_text(encoding="utf-8")
    assert "old content" not in note.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Integration test — real temp vault, section replacement
# ---------------------------------------------------------------------------


def test_vault_replace_section_integration(tmp_path: Path) -> None:
    server = _make_server(tmp_path)
    tool = get_tool(server, "vault_replace_section").fn

    vault = tmp_path / "vault"
    note = vault / "Minx" / "wiki" / "entity.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "# Amazon\n\n## Summary\n\nOld summary\n\n## Notes\n\nKeep me\n",
        encoding="utf-8",
    )

    result = tool("Minx/wiki/entity.md", "Summary", "Updated summary text")

    assert result["success"] is True
    text = note.read_text(encoding="utf-8")
    assert "## Summary\n\nUpdated summary text" in text
    assert "## Notes\n\nKeep me" in text
    assert "Old summary" not in text


def test_vault_replace_section_appends_new_section(tmp_path: Path) -> None:
    server = _make_server(tmp_path)
    tool = get_tool(server, "vault_replace_section").fn

    vault = tmp_path / "vault"
    note = vault / "Minx" / "review.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Review: 2026-04-18\n\n## Summary\n\nExisting\n", encoding="utf-8")

    result = tool("Minx/review.md", "Highlights", "- Spent less on dining")

    assert result["success"] is True
    text = note.read_text(encoding="utf-8")
    assert "## Highlights" in text
    assert "Spent less on dining" in text
    assert "## Summary\n\nExisting" in text


# ---------------------------------------------------------------------------
# Security test — rejects paths outside allowed roots
# ---------------------------------------------------------------------------


def test_vault_replace_section_rejects_outside_allowed_roots(tmp_path: Path) -> None:
    server = _make_server(tmp_path)
    tool = get_tool(server, "vault_replace_section").fn

    result = tool("../escape.md", "Summary", "evil")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"


def test_vault_replace_section_rejects_non_minx_root(tmp_path: Path) -> None:
    server = _make_server(tmp_path)
    tool = get_tool(server, "vault_replace_section").fn

    result = tool("Finance/notes.md", "Summary", "nope")

    assert result["success"] is False
    assert result["error_code"] == "INVALID_INPUT"
