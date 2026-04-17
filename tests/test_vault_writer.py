from pathlib import Path

import pytest

from minx_mcp.contracts import InvalidInputError
from minx_mcp.vault_writer import VaultWriter


def test_vault_writer_rejects_paths_outside_allowed_dirs(tmp_path):
    writer = VaultWriter(tmp_path, ("Finance",))

    with pytest.raises(InvalidInputError) as excinfo:
        writer.write_markdown("../bad.md", "nope")
    assert "outside allowed vault roots" in str(excinfo.value)


def test_vault_writer_rejects_path_traversal_within_allowed_root(tmp_path):
    writer = VaultWriter(tmp_path, ("Finance",))

    with pytest.raises(InvalidInputError) as excinfo:
        writer.write_markdown("Finance/../bad.md", "nope")
    assert "outside allowed vault roots" in str(excinfo.value)


def test_vault_writer_rejects_absolute_paths(tmp_path):
    writer = VaultWriter(tmp_path, ("Finance",))

    with pytest.raises(InvalidInputError) as excinfo:
        writer.write_markdown("/etc/passwd", "nope")
    assert "must be relative" in str(excinfo.value)


def test_replace_section_updates_named_heading(tmp_path):
    writer = VaultWriter(tmp_path, ("Finance",))

    writer.write_markdown(
        "Finance/weekly.md",
        "# Weekly\n\n## Summary\n\nOld value\n\n## Notes\n\nKeep me\n",
    )

    path = writer.replace_section("Finance/weekly.md", "Summary", "New value")
    text = path.read_text()

    assert "## Summary\n\nNew value" in text
    assert "## Notes\n\nKeep me" in text


def test_replace_section_ignores_heading_text_inside_fenced_code_block(tmp_path):
    writer = VaultWriter(tmp_path, ("Finance",))

    writer.write_markdown(
        "Finance/weekly.md",
        "# Weekly\n\n```md\n## Summary\n\nDo not touch\n```\n\n## Summary\n\nOld value\n\n## Notes\n\nKeep me\n",
    )

    path = writer.replace_section("Finance/weekly.md", "Summary", "New value")
    text = path.read_text()

    assert "```md\n## Summary\n\nDo not touch\n```" in text
    assert "## Summary\n\nNew value" in text
    assert "## Notes\n\nKeep me" in text


def test_write_markdown_avoids_direct_write_to_target_path(tmp_path, monkeypatch):
    writer = VaultWriter(tmp_path, ("Finance",))
    target = (tmp_path / "Finance" / "report.md").resolve()
    original_write_text = Path.write_text

    def guarded_write_text(self: Path, content: str, *args, **kwargs):
        if self.resolve() == target:
            raise AssertionError("target path was written directly")
        return original_write_text(self, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", guarded_write_text)

    path = writer.write_markdown("Finance/report.md", "new")

    assert path == target
    assert path.read_text() == "new"


def test_replace_section_avoids_direct_write_to_target_path(tmp_path, monkeypatch):
    writer = VaultWriter(tmp_path, ("Finance",))
    writer.write_markdown("Finance/report.md", "# Doc\n\n## Section\n\nold\n")
    target = (tmp_path / "Finance" / "report.md").resolve()
    original_write_text = Path.write_text

    def guarded_write_text(self: Path, content: str, *args, **kwargs):
        if self.resolve() == target:
            raise AssertionError("target path was written directly")
        return original_write_text(self, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", guarded_write_text)

    path = writer.replace_section("Finance/report.md", "Section", "new body")

    assert path == target
    assert "new body" in path.read_text()
