import fcntl
import os
import threading
import time
from pathlib import Path

import pytest

from minx_mcp.contracts import CONFLICT, ConflictError, InvalidInputError
from minx_mcp.vault_reader import VaultReader
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


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX symlinks")
def test_vault_writer_rejects_symlink_escape(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / "Finance").symlink_to(outside, target_is_directory=True)
    writer = VaultWriter(vault, ("Finance",))
    with pytest.raises(InvalidInputError, match="outside the vault root"):
        writer.write_markdown("Finance/leak.md", "nope")


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


def test_replace_section_is_bom_safe(tmp_path: Path) -> None:
    note = tmp_path / "Finance" / "bom.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    initial = b"\xef\xbb\xbf---\nkey: x\n---\n\n## X\n\nold\n"
    note.write_bytes(initial)

    writer = VaultWriter(tmp_path, ("Finance",))
    writer.replace_section("Finance/bom.md", "X", "new")

    out_bytes = note.read_bytes()
    assert not out_bytes.startswith(b"\xef\xbb\xbf")
    text = out_bytes.decode("utf-8")
    assert text.count("## X\n") == 1
    assert "new" in text
    assert "old" not in text

    reader = VaultReader(tmp_path, ("Finance",))
    doc = reader.read_document("Finance/bom.md")
    assert doc.frontmatter["key"] == "x"


def test_concurrent_replace_section_serializes_with_file_lock(tmp_path: Path, monkeypatch) -> None:
    note = tmp_path / "Finance" / "shared.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Doc\n\n## X\n\nseed\n", encoding="utf-8")

    original_read_text = Path.read_text

    def read_text_with_delay(self: Path, *args, **kwargs):
        result = original_read_text(self, *args, **kwargs)
        if self.resolve() == note.resolve():
            time.sleep(0.15)
        return result

    monkeypatch.setattr(Path, "read_text", read_text_with_delay)

    errors: list[BaseException] = []

    def run(body: str) -> None:
        try:
            w = VaultWriter(tmp_path, ("Finance",))
            w.replace_section("Finance/shared.md", "X", body)
        except BaseException as exc:
            errors.append(exc)

    t_a = threading.Thread(target=run, args=("A",))
    t_b = threading.Thread(target=run, args=("B",))
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    assert not errors
    final = note.read_text(encoding="utf-8")
    assert final.count("## X\n") == 1
    assert ("A" in final and "B" not in final) or ("B" in final and "A" not in final)


def test_lock_timeout_raises_conflict(tmp_path: Path) -> None:
    note = tmp_path / "Finance" / "locked.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Doc\n\n## X\n\nold\n", encoding="utf-8")
    lock_path = note.parent / ".locked.md.lock"

    hold = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with open(lock_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            hold.set()
            release.wait(timeout=30)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    t = threading.Thread(target=hold_lock)
    t.start()
    assert hold.wait(timeout=5)

    writer = VaultWriter(tmp_path, ("Finance",))
    try:
        with pytest.raises(ConflictError) as excinfo:
            writer.replace_section("Finance/locked.md", "X", "new")
        assert "locked" in str(excinfo.value).lower()
        assert excinfo.value.error_code == CONFLICT
    finally:
        release.set()
        t.join(timeout=5)


def test_lock_file_persists_between_writes(tmp_path: Path) -> None:
    writer = VaultWriter(tmp_path, ("Finance",))
    rel = "Finance/persist.md"
    lock_path = tmp_path / "Finance" / ".persist.md.lock"

    writer.write_markdown(rel, "one")
    assert lock_path.is_file()
    st1 = lock_path.stat()

    writer.write_markdown(rel, "two")
    st2 = lock_path.stat()
    assert st2.st_ino == st1.st_ino


def test_write_markdown_uses_same_lock(tmp_path: Path, monkeypatch) -> None:
    note = tmp_path / "Finance" / "mix.md"
    note.parent.mkdir(parents=True, exist_ok=True)

    original_read_text = Path.read_text

    def read_text_with_delay(self: Path, *args, **kwargs):
        result = original_read_text(self, *args, **kwargs)
        if self.resolve() == note.resolve():
            time.sleep(0.12)
        return result

    monkeypatch.setattr(Path, "read_text", read_text_with_delay)

    errors: list[BaseException] = []

    def full_write() -> None:
        try:
            VaultWriter(tmp_path, ("Finance",)).write_markdown(
                "Finance/mix.md",
                "# Doc\n\n## X\n\nfrom_write\n",
            )
        except BaseException as exc:
            errors.append(exc)

    def section_write() -> None:
        try:
            VaultWriter(tmp_path, ("Finance",)).replace_section("Finance/mix.md", "X", "from_replace")
        except BaseException as exc:
            errors.append(exc)

    t1 = threading.Thread(target=full_write)
    t2 = threading.Thread(target=section_write)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors
    text = note.read_text(encoding="utf-8")
    assert text.count("## X\n") == 1
    assert ("from_write" in text and "from_replace" not in text) or (
        "from_replace" in text and "from_write" not in text
    )
