from __future__ import annotations

from pathlib import Path

from minx_mcp.core.server import create_core_server
from minx_mcp.db import get_connection
from minx_mcp.vault_reader import VaultReader
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


def test_vault_replace_frontmatter_replaces_existing_block_and_preserves_body(
    tmp_path: Path,
) -> None:
    server = _make_server(tmp_path)
    tool = get_tool(server, "vault_replace_frontmatter").fn

    vault = tmp_path / "vault"
    note = vault / "Minx" / "Memory" / "timezone.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\n"
        "type: old\n"
        "---\n"
        "# Timezone\n\n"
        "## Human Editable\n\n"
        "Keep this prose exactly.\n",
        encoding="utf-8",
    )

    result = tool(
        "Minx/Memory/timezone.md",
        {
            "type": "minx-memory",
            "scope": "core",
            "memory_key": "core.preference.timezone",
            "memory_type": "preference",
            "subject": "timezone",
            "memory_id": 12,
            "payload_json": {"category": "timezone", "value": "America/Chicago"},
            "tags": ["minx", "memory"],
        },
    )

    assert result["success"] is True
    assert note.read_text(encoding="utf-8") == (
        "---\n"
        "type: minx-memory\n"
        "scope: core\n"
        "memory_key: core.preference.timezone\n"
        "memory_type: preference\n"
        "subject: timezone\n"
        "memory_id: 12\n"
        "payload_json: '{\"category\": \"timezone\", \"value\": \"America/Chicago\"}'\n"
        "tags: '[\"minx\", \"memory\"]'\n"
        "---\n"
        "# Timezone\n\n"
        "## Human Editable\n\n"
        "Keep this prose exactly.\n"
    )


def test_vault_replace_frontmatter_prepends_when_missing(tmp_path: Path) -> None:
    server = _make_server(tmp_path)
    tool = get_tool(server, "vault_replace_frontmatter").fn

    vault = tmp_path / "vault"
    note = vault / "Minx" / "plain.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Plain\n\nBody stays.\n", encoding="utf-8")

    result = tool("Minx/plain.md", {"type": "minx-wiki", "wiki_type": "entity"})

    assert result["success"] is True
    assert note.read_text(encoding="utf-8") == (
        "---\n"
        "type: minx-wiki\n"
        "wiki_type: entity\n"
        "---\n"
        "# Plain\n\n"
        "Body stays.\n"
    )


def test_vault_replace_frontmatter_roundtrips_apostrophes_in_json_payload(
    tmp_path: Path,
) -> None:
    server = _make_server(tmp_path)
    tool = get_tool(server, "vault_replace_frontmatter").fn
    note = tmp_path / "vault" / "Minx" / "Memory" / "phone.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Phone\n", encoding="utf-8")

    result = tool(
        "Minx/Memory/phone.md",
        {
            "type": "minx-memory",
            "payload_json": {"category": "preference", "value": "Akash's phone"},
        },
    )

    assert result["success"] is True
    doc = VaultReader(tmp_path / "vault", ("Minx",)).read_document("Minx/Memory/phone.md")
    assert doc.frontmatter["payload_json"] == (
        '{"category": "preference", "value": "Akash\'s phone"}'
    )


def test_vault_replace_frontmatter_roundtrips_escaped_multiline_strings(
    tmp_path: Path,
) -> None:
    server = _make_server(tmp_path)
    tool = get_tool(server, "vault_replace_frontmatter").fn
    note = tmp_path / "vault" / "Minx" / "Memory" / "subject.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Subject\n", encoding="utf-8")

    result = tool(
        "Minx/Memory/subject.md",
        {"type": "minx-memory", "subject": "first line\nsecond\tline\rthird"},
    )

    assert result["success"] is True
    doc = VaultReader(tmp_path / "vault", ("Minx",)).read_document("Minx/Memory/subject.md")
    assert doc.frontmatter["subject"] == "first line\nsecond\tline\rthird"
