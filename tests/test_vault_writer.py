from minx_mcp.vault_writer import VaultWriter


def test_vault_writer_rejects_paths_outside_allowed_dirs(tmp_path):
    writer = VaultWriter(tmp_path, ("Finance",))

    try:
        writer.write_markdown("../bad.md", "nope")
    except ValueError as exc:
        assert "outside allowed vault roots" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_vault_writer_rejects_path_traversal_within_allowed_root(tmp_path):
    writer = VaultWriter(tmp_path, ("Finance",))

    try:
        writer.write_markdown("Finance/../bad.md", "nope")
    except ValueError as exc:
        assert "outside allowed vault roots" in str(exc)
    else:
        raise AssertionError("expected ValueError")


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
